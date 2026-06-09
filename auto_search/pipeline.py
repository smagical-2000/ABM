"""Discovery pipeline orchestration — connector → dedup → qualify.

This is the seam between raw signals and qualified companies. It owns the
one rule that fixes the "same company processed repeatedly" problem:

    A company is qualified ONCE per run, no matter how many raw signals
    mention it. WARN data lists a company separately for each site/date,
    so without this we'd pay Claude N times for one company.

Dedup happens on `RawSignal.company_key` (the normalized name). All signals
for a company are grouped; the qualifier runs on a single representative
signal; every signal in the group is retained for storage/provenance.

The pipeline is storage-agnostic: it yields CompanyCandidate objects. A
caller (CLI test today, DB writer tomorrow) decides what to do with them.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime

from auto_search.connectors.base import SignalConnector
from auto_search.models import CompanyCandidate, RawSignal
from auto_search.qualifier import qualify

logger = logging.getLogger(__name__)

# A cooperative checkpoint awaited BEFORE each (paid) company qualification.
# Returns True to proceed, False to stop the run cleanly. An implementation can
# also block inside it (await) to PAUSE the run between companies. Checked at the
# company boundary because that is the expensive unit (one Claude call each), so
# pause/cancel never interrupts a call mid-flight or wastes spend.
RunGate = Callable[[], Awaitable[bool]]

# A predicate: given a company_key, has it already been qualified before?
# Typically `repo.already_qualified`. Kept as a bare callable so the pipeline
# depends on a function shape, not on the repository class (looser coupling).
AlreadyQualified = Callable[[str], bool]

# An optional async filter over the full pulled-signal list, applied BEFORE
# grouping. Used by the jobs flow to inject the cheap job-level qualifier
# (drop postings that aren't the RCM role we want) so the expensive company
# qualifier only ever runs on companies with a real signal. Shape, not class.
SignalPrefilter = Callable[[list[RawSignal]], Awaitable[list[RawSignal]]]

# An optional per-company predicate, checked AFTER grouping and BEFORE the
# (expensive) company qualifier: return True to DEFER the company — skip
# qualification this run without marking it decided, so it's re-evaluated next
# run. The jobs flow uses this for signal-stacking (park a single low-tier
# posting until the company stacks). Connector-agnostic: the pipeline doesn't
# know WHY a company is deferred, only that it should hold. `on_defer` is the
# matching side-effect hook (e.g. persist the company to a watch ledger).
DeferCompany = Callable[[str, list[RawSignal]], bool]
OnDefer = Callable[[str, list[RawSignal]], None]


async def collect_unique_companies(
    connector: SignalConnector,
    since: datetime,
    *,
    limit: int | None = None,
    prefilter: SignalPrefilter | None = None,
) -> OrderedDict[str, list[RawSignal]]:
    """Pull signals and group them by company, preserving first-seen order.

    Returns an ordered map of company_key -> [signals]. Grouping here means
    the qualifier never sees the same company twice. `limit` caps the number
    of UNIQUE companies (not raw signals) — useful for cheap test runs.

    `prefilter` (optional) runs over ALL pulled signals before grouping — the
    seam for the job-level qualifier. It must be order-preserving.
    """
    signals = [s async for s in connector.pull(since=since)]
    if prefilter is not None:
        signals = await prefilter(signals)

    groups: OrderedDict[str, list[RawSignal]] = OrderedDict()
    for signal in signals:
        key = signal.company_key
        if not key:
            logger.debug("skipping signal with empty company key: %r",
                         signal.company_name_raw)
            continue

        if key in groups:
            groups[key].append(signal)
            continue

        # New company — respect the unique-company limit.
        if limit is not None and len(groups) >= limit:
            continue
        groups[key] = [signal]

    logger.info(
        "collected %d unique companies from %d connector signals",
        len(groups), sum(len(v) for v in groups.values()),
    )
    return groups


async def run(
    connector: SignalConnector,
    since: datetime,
    *,
    limit: int | None = None,
    skip_already_qualified: AlreadyQualified | None = None,
    prefilter: SignalPrefilter | None = None,
    defer: DeferCompany | None = None,
    on_defer: OnDefer | None = None,
    on_plan: Callable[[int], None] | None = None,
    gate: RunGate | None = None,
) -> AsyncIterator[CompanyCandidate]:
    """Run the full discovery pipeline, yielding one candidate per company.

    Steps:
      1. Pull signals; optionally `prefilter` them (e.g. the job qualifier).
      2. Group by company (within-run dedup).
      3. Optionally skip companies already decided in a PRIOR run
         (cross-run dedup) — pass a repo's `already_qualified` here.
      4. Optionally `defer` a company (skip qualify WITHOUT marking it decided,
         so it's re-checked next run) — the jobs stacking gate; `on_defer`
         records it to a watch ledger.
      5. Qualify each remaining company once (website research via Claude).
      6. Yield a CompanyCandidate (company + signals + verdict).

    The two dedup layers together guarantee one Claude call per company,
    ever: grouping handles repeats within a run, `skip_already_qualified`
    handles repeats across runs. `defer` is orthogonal — a not-yet decision
    that costs nothing and recurs. Callers persist/display the candidates;
    the pipeline has no opinion on storage.
    """
    groups = await collect_unique_companies(
        connector, since, limit=limit, prefilter=prefilter)

    # The qualification denominator: unique companies MINUS the ones already
    # decided in a prior run (those cost nothing and stream past instantly).
    # Reported once, up front, so the UI can show "X of N · Y%" while the
    # expensive per-company Claude calls run.
    if on_plan is not None:
        planned = sum(
            1 for key, sigs in groups.items()
            if (skip_already_qualified is None or not skip_already_qualified(key))
            and not (defer is not None and defer(key, sigs))
        )
        try:
            on_plan(planned)
        except Exception:  # noqa: BLE001 — progress reporting must never break a run
            logger.exception("on_plan hook failed")

    skipped = 0
    deferred = 0
    for key, signals in groups.items():
        if skip_already_qualified is not None and skip_already_qualified(key):
            skipped += 1
            logger.debug("skip already-qualified company: %s", key)
            continue

        # Cooperative pause/cancel checkpoint, BEFORE we spend on this company.
        # The gate may block (pause) and returns False to stop the run cleanly.
        if gate is not None and not await gate():
            logger.info("run gate stopped the pipeline before %s — %d done, "
                        "remaining skipped", key, 0)
            break

        # Cost gate: a connector may DEFER a company (e.g. jobs signal-stacking
        # parks a single low-tier posting). Deferred companies aren't qualified
        # and aren't marked decided, so they're re-evaluated next run.
        if defer is not None and defer(key, signals):
            deferred += 1
            if on_defer is not None:
                try:
                    on_defer(key, signals)
                except Exception:  # noqa: BLE001 — watch ledger must not break a run
                    logger.exception("on_defer hook failed for %s", key)
            logger.debug("defer (park) company: %s", key)
            continue

        representative = max(signals, key=lambda s: s.signal_strength)
        verdict = await qualify(representative)
        yield CompanyCandidate(
            company_key=key,
            company_name=representative.company_name_raw,
            signals=signals,
            qualification=verdict,
        )

    if skipped:
        logger.info("skipped %d companies already qualified in prior runs", skipped)
    if deferred:
        logger.info("deferred %d companies (parked, watched for stacking)", deferred)
