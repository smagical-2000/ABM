"""Shared types + constants for the Auto Search module.

Everything here is source-agnostic: a RawSignal looks the same whether it
came from warntracker, a funding feed, or a leadership-change feed. Keeping
these definitions in one place is what lets connectors and the qualifier
stay decoupled.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from auto_search.normalize import normalize_company_name, parse_int_loose

# ── shared constants ──────────────────────────────────────────────────

# Minimum headcount for a layoff to be worth evaluating. Below this, the
# event is too small to imply the kind of operational pain we sell into.
# Lives here (not in a connector) so the connector's structural filter and
# the qualifier's defence-in-depth check can never drift apart.
MIN_LAID_OFF = 10

# ── enums ─────────────────────────────────────────────────────────────

Segment = Literal["specialty", "payer", "health_system"]

CompanyType = Literal[
    "provider", "payer", "vendor", "tech", "pharma", "device",
    "biotech", "government", "consumer", "other", "unknown",
]

# Machine verdict status — used by the DB layer to drive the review queue.
IcpStatus = Literal["pending", "qualified", "needs_review", "disqualified"]

DecidedBy = Literal["rules", "llm", "rules+llm"]


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
        """Canonical dedup key — the same for every signal about a company.

        This is what the pipeline groups on so one company is qualified
        once, no matter how many raw signals mention it.
        """
        return normalize_company_name(self.company_name_raw)


class QualificationResult(BaseModel):
    """The qualifier's verdict on whether a company fits Magical's ICP.

    Produced by Claude after researching the company's website with the
    web_search tool. `evidence_url` is the page the verdict leaned on —
    essential for a human reviewer to sanity-check a borderline call.
    """

    qualified: bool
    segment: Segment | None = None
    sub_segment: str | None = None
    company_type: CompanyType = "unknown"
    approximate_employees: int | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    evidence_url: str | None = None
    needs_human_review: bool = False
    decided_by: DecidedBy = "llm"

    @field_validator("approximate_employees", mode="before")
    @classmethod
    def _coerce_employees(cls, v: object) -> int | None:
        """Accept messy LLM values like "2,400" or "~2400 employees".

        Without this, a cosmetically-formatted number would fail int
        validation and wrongly force the whole verdict into needs_review.
        """
        return parse_int_loose(v)
