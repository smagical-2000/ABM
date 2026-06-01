"""M&A connector — SignalBase acquisitions feed.

Detects US healthcare PROVIDERS/PAYERS that were just acquired — a company
"in transition" is a buying signal (new ownership → tech-stack review,
integration pain, budget reset). Deterministic: every record carries an
`occurredAt`/`announcedDate`, so recency is a comparison.

Subject of the signal
----------------------
The ACQUIRED company (the target) — per Galyna's call that the company getting
acquired is the one in play. The acquirer is kept in the payload for context
(a health system rolling up practices is itself an integration-pain story we
may mine later, but it's not the signal subject today).

Filtering
---------
Server-side: `categories` (healthcare LinkedIn industries) + `countries=US` +
date. Client-side authority: `is_healthcare_provider()` on the acquired
company's industry + subcategory — this excludes pharma/biotech deals that
LinkedIn mislabels as "Hospitals and Health Care" (e.g. a vaccine startup
bought by a pharma giant).

Cost: SignalBase bills per record (~$20/1000). The connector pages newest-first
and stops at the date cutoff; keep `per_page` small when testing.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import AsyncIterator
from datetime import datetime

from auto_search.clients.signalbase import AcquisitionRecord, SignalBaseClient
from auto_search.healthcare import CATEGORIES_FILTER, is_healthcare_provider
from auto_search.models import RawSignal
from auto_search.normalize import clean_domain, parse_iso_datetime, slugify

logger = logging.getLogger(__name__)


class AcquisitionsConnector:
    """Pull recent US healthcare acquisitions from SignalBase."""

    source_name = "signalbase_acquisitions"
    signal_types = ["acquisition"]
    default_cron = "0 8 * * *"  # 08:00 UTC daily

    def __init__(
        self,
        *,
        client: SignalBaseClient | None = None,
        max_pages: int = 1,
        per_page: int = 5,
    ) -> None:
        # COST ≈ per_page × max_pages records. Keep small for testing.
        self._client = client or SignalBaseClient()
        self._max_pages = max_pages
        self._per_page = per_page

    async def pull(self, since: datetime) -> AsyncIterator[RawSignal]:
        """Yield an acquisition signal per US healthcare provider/payer that
        was acquired on/after `since`.
        """
        drops: Counter[str] = Counter()
        yielded = 0
        crossed_cutoff = False

        records = self._client.iter_acquisitions(
            categories=CATEGORIES_FILTER,
            countries="US",
            date_preset=_since_to_preset(since),
            per_page=self._per_page,
            max_pages=self._max_pages,
        )

        async for rec in records:
            signal, reason = _record_to_signal(rec, since)
            if signal is None:
                drops[reason] += 1
                if reason == "before_window":
                    crossed_cutoff = True
                    break
                continue
            yielded += 1
            yield signal

        logger.info(
            "signalbase_acquisitions pull done — yielded=%d%s",
            yielded, " (stopped at date cutoff)" if crossed_cutoff else "",
        )
        for reason, n in drops.most_common():
            logger.info("  dropped %d  %s", n, reason)


# ── record → signal ───────────────────────────────────────────────────


def _record_to_signal(
    rec: AcquisitionRecord, since: datetime
) -> tuple[RawSignal | None, str]:
    """Map an acquisition to a signal about the ACQUIRED company, or drop it."""
    company = (rec.companyName or "").strip()
    if not company:
        return None, "missing_company"

    observed_at = parse_iso_datetime(rec.occurredAt or rec.announcedDate)
    if observed_at is None:
        return None, "unparseable_date"
    if observed_at < since:
        return None, "before_window"

    if (rec.companyCountry or "").upper() not in ("US", ""):
        return None, "non_us"

    # Authority gate: excludes pharma/biotech mislabelled as healthcare.
    if not is_healthcare_provider(rec.companyIndustry, rec.companySubcategory):
        return None, "not_healthcare"

    return (
        RawSignal(
            source="signalbase_acquisitions",
            source_external_id=rec.signalId or _fallback_id(rec, observed_at),
            signal_type="acquisition",
            company_name_raw=company,
            company_domain_raw=clean_domain(rec.companyWebsite),
            observed_at=observed_at,
            signal_strength=_signal_strength(rec),
            payload={
                "acquirer_name": rec.acquiringCompanyName,
                "acquirer_website": clean_domain(rec.acquiringCompanyWebsite),
                "acquirer_industry": rec.acquiringCompanyIndustry,
                "deal_amount_usd": rec.amount,
                "deal_currency": rec.currency,
                "percentage_acquired": rec.percentage,
                "announced_date": rec.announcedDate,
                "occurred_at": rec.occurredAt,
                "target_industry": rec.companyIndustry,
                "target_subcategory": rec.companySubcategory,
                "target_employees": rec.companyEmployeeCount,
                "target_description": rec.companyDescription,
                "source_urls": _source_urls(rec.sources),
            },
        ),
        "",
    )


def _signal_strength(rec: AcquisitionRecord) -> float:
    """Bigger acquired org = more revenue-cycle surface = stronger signal.
    Acquisitions are inherently notable, so the floor is fairly high.
    """
    n = rec.companyEmployeeCount
    if n is None:
        return 0.70
    if n >= 1000:
        return 0.90
    if n >= 200:
        return 0.80
    if n >= 50:
        return 0.70
    return 0.60


def _source_urls(sources: list[dict] | None) -> list[str]:
    if not sources:
        return []
    return [s.get("url") for s in sources if isinstance(s, dict) and s.get("url")]


def _fallback_id(rec: AcquisitionRecord, observed_at: datetime) -> str:
    acquirer = slugify(rec.acquiringCompanyName or "unknown")
    target = slugify(rec.companyName or "unknown")
    return f"{acquirer}__acquires__{target}::{observed_at.date().isoformat()}"


def _since_to_preset(since: datetime) -> str:
    """Map a cutoff to the smallest SignalBase date_preset that covers it.

    Coarse server hint; the connector's occurredAt >= since check is the
    authority. M&A is rarer than job changes, so windows skew wider.
    """
    from datetime import UTC

    days = max(0, (datetime.now(UTC) - since).days)
    if days <= 1:
        return "today"
    if days <= 7:
        return "last_7d"
    if days <= 14:
        return "last_14d"
    if days <= 30:
        return "last_30d"
    if days <= 60:
        return "last_60d"
    if days <= 90:
        return "last_90d"
    if days <= 180:
        return "last_6m"
    return "last_1y"


# ── manual CLI trigger ────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    import sys
    from datetime import UTC, timedelta

    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)-7s  %(message)s")

    # Usage: python -m auto_search.connectors.acquisitions [days] [limit] [pages]
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    pages = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    since = datetime.now(UTC) - timedelta(days=days)
    print(f"\nSignalBase acquisitions since {since.date()} ({days}d) — "
          f"≈ {limit * pages} record-credit(s)\n")

    async def _run() -> None:
        connector = AcquisitionsConnector(max_pages=pages, per_page=limit)
        n = 0
        async for sig in connector.pull(since=since):
            n += 1
            p = sig.payload
            amt = p.get("deal_amount_usd")
            amt_s = f"${amt:,}" if isinstance(amt, int) else "undisclosed"
            print(f"  {n:>3} {sig.company_name_raw[:30]:30} "
                  f"acquired by {str(p.get('acquirer_name'))[:24]:24} "
                  f"{amt_s:>14}  {str(p.get('target_industry'))[:20]:20} "
                  f"{str(p.get('occurred_at'))[:10]} s={sig.signal_strength}")
        print(f"\nDone — {n} healthcare acquisition signals.\n")

    asyncio.run(_run())
