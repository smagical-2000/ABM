"""Ingest one social engager into the discovery pipeline.

The gauntlet, cheapest checks first so we never spend an LLM call we don't have
to (the cost pattern the jobs flow already uses):

    1. drop Magical's own employees                     (pure)
    2. require Director & above                          (pure)
    3. for event signals, require confirmed attendance   (pure)
    4. require a resolvable company name                 (pure)
    5. company already qualified? → just append the signal (append-only, no LLM)
       otherwise → run the existing website ICP qualifier, then save

The person rides in the signal payload (rendered as a contact in the company
drawer); the COMPANY is what gets qualified, scored, and ABM-matched — identical
to every other discovery signal.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from auto_search.models import CompanyCandidate, QualificationResult, RawSignal
from auto_search.normalize import normalize_company_name
from auto_search.qualifier import qualify
from auto_search.scoring import spend_guard
from auto_search.social.filters import is_attending, is_magical
from auto_search.social.models import Engager, IngestResult
from auto_search.social.seniority import is_decision_maker

logger = logging.getLogger(__name__)

# The qualifier seam, injectable for tests (default = the real website qualifier).
Qualify = Callable[[RawSignal], Awaitable[QualificationResult]]
# Gate consulted ONLY before a NEW (paid) qualification. Returns a skip-reason
# string to refuse (e.g. "budget_blocked" / "request_cap"), or None to allow.
QualifyGate = Callable[[], str | None]
# Looks up whether a company is on the ABM target list, by name (+ optional
# domain). Returns a match (duck-typed: .target_name / .segment / .source_sheet)
# or None. Injected so the social flow can treat a tracked account as
# authoritative without importing the abm package.
AbmLookup = Callable[[str, str | None], object | None]


def _skip(reason: str) -> IngestResult:
    return IngestResult(accepted=False, action="skipped", reason=reason)


async def ingest_engager(
    engager: Engager,
    *,
    repo,
    qualify_fn: Qualify = qualify,
    op: spend_guard.Operation | None = None,
    can_qualify: QualifyGate | None = None,
    abm_lookup: AbmLookup | None = None,
) -> IngestResult:
    """Run one engager through the gauntlet and persist if it survives.

    `repo` is a DiscoveryRepository (needs already_qualified / add_signal /
    save_candidate). `op` records the (paid) qualify spend as a cost_event so it
    counts toward the discovery budget. `can_qualify` is checked only before a
    NEW qualification — it lets the caller enforce a per-request cap / budget
    without re-deriving which records are new. Never raises for ordinary
    rejections — returns a skipped IngestResult for per-record reporting.
    """
    # Magical's own staff: match the company only (NOT the person's profile URL,
    # which would false-positive on personal sites / pasted Magical links).
    if is_magical(engager.company_name, engager.company_website):
        return _skip("magical_employee")

    dm, _why = is_decision_maker(
        engager.job_title, engager.job_title_levels, engager.job_title_role)
    if not dm:
        return _skip("not_decision_maker")

    if engager.source == "event" and not is_attending(
            engager.comment_text, engager.post_title)[0]:
        return _skip("attendance_unconfirmed")

    company = (engager.company_name or "").strip()
    key = normalize_company_name(company)
    if not key:
        return _skip("no_company")

    signal = engager.to_signal()

    # Already in discovery → append-only: add the new engager's signal without
    # re-paying the qualifier or churning the existing verdict. Always free.
    if repo.already_qualified(key):
        added = repo.add_signal(key, signal)
        return IngestResult(
            accepted=True, action="appended" if added else "duplicate",
            reason="already_qualified", company_key=key, company_name=company)

    # On the ABM target list → authoritative. We already chose to target this
    # account, so a decision-maker engaging is itself the buying signal: save it
    # as qualified WITHOUT the paid ICP qualifier (the list IS the qualification).
    # A tracked target is never lost on an ICP miss, and we don't pay to
    # re-research a company we picked. The panel's ABM badge does the highlight.
    abm = abm_lookup(company, engager.company_website) if abm_lookup else None
    if abm is not None:
        verdict = QualificationResult(
            qualified=True,
            segment=_abm_segment(abm),
            company_type="provider",
            confidence=0.9,
            reasoning=_abm_reason(abm, engager),
            domain=engager.company_website,
            decided_by="rules",
        )
        repo.save_candidate(CompanyCandidate(
            company_key=key, company_name=company, signals=[signal], qualification=verdict))
        logger.info("social ingest: %s → qualified via ABM target list (%s)",
                    company, engager.source)
        return IngestResult(accepted=True, action="qualified", reason="qualified",
                            company_key=key, company_name=company)

    # New company → a PAID qualification. Let the caller veto first (budget/cap).
    if can_qualify is not None and (blocked := can_qualify()):
        return _skip(blocked)

    verdict = await qualify_fn(signal)
    # Record measured qualify spend so it lands in the discovery cost meter
    # (step='qualify') — the same accounting every other paid path uses.
    if op is not None and verdict.llm_spend:
        s = verdict.llm_spend
        op.record(step="qualify", actual_usd=s.cost_usd, company_key=key,
                  model=s.model, searches=s.searches,
                  metadata={"input_tokens": s.input_tokens,
                            "output_tokens": s.output_tokens,
                            "measured": True, "source": engager.source})
    repo.save_candidate(CompanyCandidate(
        company_key=key, company_name=company, signals=[signal], qualification=verdict))
    logger.info("social ingest: %s → %s (%s)", company, verdict.to_status(), engager.source)
    return IngestResult(
        accepted=True, action="qualified", reason=verdict.to_status(),
        company_key=key, company_name=company)


# ── ABM-target verdict helpers ─────────────────────────────────────────

# Map the ABM workbook's sheet/segment label onto the platform segment enum.
# Ordered so a more specific hint wins (physician group before hospital).
_SEGMENT_HINTS = (
    ("payer", "payer"),
    ("physician group", "specialty"),
    ("specialt", "specialty"),
    ("health system", "health_system"),
    ("hospital", "health_system"),
    ("rural", "health_system"),
)


def _abm_segment(match: object) -> str | None:
    """Best-effort platform segment from the ABM target's sheet/segment label."""
    label = (getattr(match, "segment", None) or getattr(match, "source_sheet", None) or "")
    label = label.lower()
    for hint, seg in _SEGMENT_HINTS:
        if hint in label:
            return seg
    return None


def _abm_reason(match: object, engager: Engager) -> str:
    """The 'why qualified' line for an ABM-target engagement — names the person,
    what they engaged with, and the list membership."""
    who = engager.full_name or "A decision-maker"
    title = f" ({engager.job_title})" if engager.job_title else ""
    where = {
        "competitor_post": "a competitor's LinkedIn post",
        "magical_post": "a Magical post",
        "event": f"the {engager.event_name or 'tracked'} event",
    }.get(engager.source, "a tracked post")
    verb = "is attending" if engager.source == "event" else "engaged with"
    name = getattr(match, "target_name", None) or "your list"
    return (f"On the ABM target list ({name}). {who}{title} {verb} {where} — "
            "a tracked account showing intent.")
