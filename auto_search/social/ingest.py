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


def _skip(reason: str) -> IngestResult:
    return IngestResult(accepted=False, action="skipped", reason=reason)


async def ingest_engager(
    engager: Engager,
    *,
    repo,
    qualify_fn: Qualify = qualify,
    op: spend_guard.Operation | None = None,
    can_qualify: QualifyGate | None = None,
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
