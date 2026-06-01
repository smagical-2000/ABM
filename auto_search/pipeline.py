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
from datetime import datetime
from typing import AsyncIterator, Callable

from pydantic import BaseModel

from auto_search.connectors.base import SignalConnector
from auto_search.models import QualificationResult, RawSignal
from auto_search.qualifier import qualify

logger = logging.getLogger(__name__)

# A predicate: given a company_key, has it already been qualified before?
# Typically `repo.already_qualified`. Kept as a bare callable so the pipeline
# depends on a function shape, not on the repository class (looser coupling).
AlreadyQualified = Callable[[str], bool]


class CompanyCandidate(BaseModel):
    """One unique company plus all signals seen for it and its verdict.

    This is the unit the pipeline emits and the DB persists: a deduped
    company, its provenance (every signal), and the qualifier's decision.
    """

    company_key: str                 # normalized dedup key
    company_name: str                # display name (from first signal seen)
    signals: list[RawSignal]         # every raw signal for this company
    qualification: QualificationResult

    @property
    def primary_signal(self) -> RawSignal:
        """The strongest signal — used as the representative for the company."""
        return max(self.signals, key=lambda s: s.signal_strength)


async def collect_unique_companies(
    connector: SignalConnector,
    since: datetime,
    *,
    limit: int | None = None,
) -> "OrderedDict[str, list[RawSignal]]":
    """Pull signals and group them by company, preserving first-seen order.

    Returns an ordered map of company_key -> [signals]. Grouping here means
    the qualifier never sees the same company twice. `limit` caps the number
    of UNIQUE companies (not raw signals) — useful for cheap test runs.
    """
    groups: "OrderedDict[str, list[RawSignal]]" = OrderedDict()

    async for signal in connector.pull(since=since):
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
    skip_already_qualified: "AlreadyQualified | None" = None,
) -> AsyncIterator[CompanyCandidate]:
    """Run the full discovery pipeline, yielding one candidate per company.

    Steps:
      1. Pull + group signals by company (within-run dedup).
      2. Optionally skip companies already decided in a PRIOR run
         (cross-run dedup) — pass a repo's `already_qualified` here.
      3. Qualify each remaining company once (website research via Claude).
      4. Yield a CompanyCandidate (company + signals + verdict).

    The two dedup layers together guarantee one Claude call per company,
    ever: grouping handles repeats within a run, `skip_already_qualified`
    handles repeats across runs. Callers persist/display the candidates;
    the pipeline has no opinion on storage.
    """
    groups = await collect_unique_companies(connector, since, limit=limit)

    skipped = 0
    for key, signals in groups.items():
        if skip_already_qualified is not None and skip_already_qualified(key):
            skipped += 1
            logger.debug("skip already-qualified company: %s", key)
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
