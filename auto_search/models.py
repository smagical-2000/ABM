"""Shared types + constants for the Auto Search module.

Everything here is source-agnostic: a RawSignal looks the same whether it
came from warntracker, a funding feed, or a leadership-change feed. Keeping
these definitions in one place is what lets connectors, the qualifier, the
pipeline, and the repository stay decoupled (no import cycles).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from auto_search.normalize import normalize_company_name, parse_int_loose

# ── shared constants ──────────────────────────────────────────────────

# Minimum headcount for a layoff to be worth evaluating. Below this, the
# event is too small to imply the operational pain we sell into. Lives here
# (not in a connector) so the connector's structural filter and the
# qualifier's defence-in-depth check can never drift apart.
MIN_LAID_OFF = 10

# ── segments — ONE canonical vocabulary ───────────────────────────────
#
# The discovery module speaks `specialty | payer | health_system`. The
# legacy CLI scorer (scorer.py) and Galyna's frameworks speak
# `specialties | payer | hs`. These MUST be translated at the boundary or
# promotion will load the wrong scoring framework. SEGMENT_TO_SCORER is that
# single, audited mapping — never hand-translate segments anywhere else.

Segment = Literal["specialty", "payer", "health_system"]

SEGMENT_TO_SCORER: dict[Segment, str] = {
    "specialty": "specialties",      # scorer.py --segment specialties
    "payer": "payer",                # scorer.py --segment payer
    "health_system": "hs",           # scorer.py --segment hs
}


def to_scorer_segment(segment: Segment) -> str:
    """Translate a discovery segment to the CLI scorer's segment id.

    Used at the promotion boundary so `scorer.py --segment <x>` always gets
    the value it expects. Raises on unknown input rather than silently
    scoring with the wrong framework.
    """
    try:
        return SEGMENT_TO_SCORER[segment]
    except KeyError as exc:
        raise ValueError(f"unknown discovery segment: {segment!r}") from exc


CompanyType = Literal[
    "provider", "payer", "vendor", "tech", "pharma", "device",
    "biotech", "government", "consumer", "other", "unknown",
]

# Machine verdict status — drives the review queue and keeps genuine
# disqualifications ("Apple Inc.") separate from operational failures
# ("website unreachable"). The DB CHECK constraint mirrors these values.
IcpStatus = Literal["pending", "qualified", "needs_review", "disqualified", "error"]

DecidedBy = Literal["rules", "llm", "rules+llm"]


class LlmSpend(BaseModel):
    """Measured USD + token usage for one Claude call.

    Filled by llm.spend_from_response() after each paid call. Flows through
    QualificationResult → cost_events so discovery billing is real tokens, not
    the flat DISCOVERY_EST_QUAL_COST guess.
    """

    cost_usd: float = 0.0
    model: str = ""
    searches: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


# ── core models ───────────────────────────────────────────────────────


class RawSignal(BaseModel):
    """One signal from one source, before matching or qualification.

    Every connector yields this exact shape. The `payload` dict holds
    source-specific fields (a layoff row's headcount, a funding row's
    amount) so the common columns stay stable as we add sources.
    """

    source: str                      # connector id, e.g. "warntracker"
    source_external_id: str          # stable per-event id → signal-level dedup
    signal_type: str                 # "layoff", "funding_round", …
    company_name_raw: str
    company_domain_raw: str | None = None
    signal_strength: float = Field(0.5, ge=0.0, le=1.0)
    payload: dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime            # when the event actually happened

    @property
    def company_key(self) -> str:
        """Canonical dedup key — identical for every signal about a company.

        This is what the pipeline groups on so one company is qualified
        once, regardless of how many raw signals mention it.
        """
        return normalize_company_name(self.company_name_raw)

    @property
    def summary(self) -> str:
        """Human one-liner for the 'why discovered' UI list.

        Derived from the signal-specific payload. Defined here (not in a repo)
        so every storage backend renders the same text from the same fields.
        """
        p = self.payload
        if self.signal_type == "layoff":
            n = p.get("laid_off_count")
            where = p.get("city") or p.get("state") or ""
            head = f"{n} laid off" if n else "layoff"
            return f"{head}{f' in {where}' if where else ''}".strip()
        if self.signal_type == "leadership_change":
            return p.get("new_role") or "leadership change"
        if self.signal_type == "acquisition":
            acq = p.get("acquirer_name")
            amt = p.get("deal_amount_usd")
            amt_s = f" (${amt:,})" if isinstance(amt, int) else ""
            return f"Acquired by {acq}{amt_s}" if acq else "acquisition"
        if self.signal_type == "funding_round":
            rnd = (p.get("round_type") or "funding").title()
            amt = p.get("amount_usd")
            amt_s = f"${amt:,} " if isinstance(amt, int) else ""
            return f"Raised {amt_s}{rnd}".strip()
        if self.signal_type == "job_posting":
            title = p.get("job_title") or p.get("role") or "role"
            where = p.get("location") or ""
            return f"Hiring: {title}{f' — {where}' if where else ''}".strip()
        if self.signal_type == "social_engagement":
            who = p.get("person_name") or "Someone"
            role = p.get("person_title")
            verb = "commented on" if p.get("engagement_type") == "comment" else "engaged with"
            whose = "a Magical post" if p.get("social_source") == "magical_post" \
                else "a competitor post" if p.get("social_source") == "competitor_post" \
                else "a tracked post"
            return f"{who}{f' ({role})' if role else ''} {verb} {whose}".strip()
        if self.signal_type == "event_attendance":
            who = p.get("person_name") or "Someone"
            role = p.get("person_title")
            event = p.get("event_name") or "a tracked event"
            return f"{who}{f' ({role})' if role else ''} attending {event}".strip()
        return self.signal_type


class QualificationResult(BaseModel):
    """The qualifier's verdict on whether a company fits Magical's ICP.

    Produced by Claude after researching the company's website with the
    web_search tool. `evidence_url` is the page the verdict leaned on —
    essential for a human reviewer to sanity-check a borderline call.

    `is_error` distinguishes an operational failure (parse error, LLM
    timeout, unreachable site) from a genuine low-confidence verdict on a
    real company. Both want human eyes, but they're different queues.
    """

    qualified: bool
    segment: Segment | None = None
    sub_segment: str | None = None
    company_type: CompanyType = "unknown"
    approximate_employees: int | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    evidence_url: str | None = None
    # Company's primary domain (e.g. "orthoindy.com"). Captured now so the
    # eventual promotion step can match domain-first against the accounts
    # table — the locked dedup rule for the main platform — instead of
    # re-researching the company later.
    domain: str | None = None
    needs_human_review: bool = False
    is_error: bool = False
    decided_by: DecidedBy = "llm"
    # Populated after a paid LLM call; rules-only pre-filters leave this None.
    llm_spend: LlmSpend | None = None

    @field_validator("approximate_employees", mode="before")
    @classmethod
    def _coerce_employees(cls, v: object) -> int | None:
        """Accept messy LLM values like "2,400" or "~2400 employees".

        Without this, a cosmetically-formatted number would fail int
        validation and wrongly force the whole verdict into needs_review.
        """
        return parse_int_loose(v)

    def to_status(self) -> IcpStatus:
        """Map this verdict to the single status enum used by storage + UI.

        Order matters: errors first (so failures never masquerade as
        qualified or as ordinary review items), then the qualified/review/
        disqualified split.
        """
        if self.is_error:
            return "error"
        if self.qualified and not self.needs_human_review:
            return "qualified"
        if self.needs_human_review:
            return "needs_review"
        return "disqualified"


class CompanyCandidate(BaseModel):
    """One unique company plus every signal seen for it and its verdict.

    This is the unit the pipeline emits and the repository persists: a
    deduped company, its provenance (all signals), and the qualifier's
    decision. Lives in models.py (not pipeline.py) so the repository can
    import it without creating a pipeline→repository→pipeline cycle.
    """

    company_key: str                 # normalized dedup key
    company_name: str                # display name (from strongest signal)
    signals: list[RawSignal]         # every raw signal for this company
    qualification: QualificationResult

    @property
    def primary_signal(self) -> RawSignal:
        """The strongest signal — the representative for the company."""
        return max(self.signals, key=lambda s: s.signal_strength)
