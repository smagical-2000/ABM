"""Funding connector — SignalBase funding-round feed.

Detects US healthcare PROVIDERS/PAYERS that just raised capital — fresh money
is a buying signal (budget to spend; growth/rollups → revenue-cycle pain).
Deterministic: every record carries `occurredAt`/`announcedDate`.

The catch with funding
----------------------
Funding is dominated by health-TECH vendors (sub=ai/saas) and BIOTECH — none
of which are Magical's ICP (we sell TO providers/payers, not to those vendors).
So beyond the shared healthcare gate, this connector hard-drops vendor/biotech
subcategories at the source, BEFORE the qualifier, so we don't spend a Claude
call disqualifying an AI startup. Only provider/payer-flavoured raises pass
through to qualification. Yield is therefore low by design — that's correct.

Server-side: `categories` (healthcare industries) + `countries=US` + date +
`amount_min`. Client-side authority: `is_healthcare_provider()` plus the
vendor-subcategory exclusion.

Cost: SignalBase bills per record. Keep `per_page` small when testing.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from auto_search.clients.signalbase import FundingRecord, SignalBaseClient
from auto_search.healthcare import CATEGORIES_FILTER, is_healthcare_provider
from auto_search.models import RawSignal
from auto_search.normalize import clean_domain, parse_iso_datetime, slugify

logger = logging.getLogger(__name__)

# SignalBase subcategories that mean "tech vendor / biotech", i.e. NOT a
# provider or payer. Funding rounds skew heavily toward these, so dropping them
# up front avoids paying Claude to disqualify obvious non-ICP companies.
_VENDOR_SUBCATEGORIES = frozenset({
    "ai", "saas", "software", "cybersecurity", "web3", "devtools",
    "analytics", "cloud", "iot", "fintech", "payments", "marketing",
    "advertising", "sales", "hr tech", "legal", "biotechnology", "science",
})

# Skip rounds below this size — a tiny pre-seed isn't a meaningful budget
# signal for an RCM purchase. Tunable per run.
_DEFAULT_AMOUNT_MIN = 1_000_000


class FundingConnector:
    """Pull recent US healthcare provider/payer funding rounds from SignalBase."""

    source_name = "signalbase_funding"
    signal_types = ["funding_round"]
    default_cron = "0 9 * * *"  # 09:00 UTC daily

    def __init__(
        self,
        *,
        client: SignalBaseClient | None = None,
        max_pages: int = 1,
        per_page: int = 5,
        amount_min: int = _DEFAULT_AMOUNT_MIN,
    ) -> None:
        self._client = client or SignalBaseClient()
        self._max_pages = max_pages
        self._per_page = per_page
        self._amount_min = amount_min

    async def pull(self, since: datetime) -> AsyncIterator[RawSignal]:
        """Yield a funding signal per US healthcare provider/payer that raised
        on/after `since`.
        """
        drops: Counter[str] = Counter()
        yielded = 0
        crossed_cutoff = False

        records = self._client.iter_funding(
            categories=CATEGORIES_FILTER,
            countries="US",
            date_preset=_since_to_preset(since),
            amount_min=self._amount_min,
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
            "signalbase_funding pull done — yielded=%d%s",
            yielded, " (stopped at date cutoff)" if crossed_cutoff else "",
        )
        for reason, n in drops.most_common():
            logger.info("  dropped %d  %s", n, reason)


# ── record → signal ───────────────────────────────────────────────────


def _record_to_signal(
    rec: FundingRecord, since: datetime
) -> tuple[RawSignal | None, str]:
    """Map a funding round to a signal about the raising company, or drop it."""
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

    # Drop tech-vendor / biotech raises before they reach the qualifier.
    if (rec.companySubcategory or "").strip().lower() in _VENDOR_SUBCATEGORIES:
        return None, "vendor_not_provider"

    if not is_healthcare_provider(rec.companyIndustry, rec.companySubcategory):
        return None, "not_healthcare"

    return (
        RawSignal(
            source="signalbase_funding",
            source_external_id=rec.signalId or _fallback_id(rec, observed_at),
            signal_type="funding_round",
            company_name_raw=company,
            company_domain_raw=clean_domain(rec.companyWebsite),
            observed_at=observed_at,
            signal_strength=_signal_strength(rec.amount),
            payload={
                "round_type": rec.roundType,
                "round_flavor": rec.roundFlavor,
                "amount_usd": rec.amount,
                "currency": rec.currency,
                "investors": _investor_names(rec.investors),
                "verification_status": rec.verificationStatus,
                "announced_date": rec.announcedDate,
                "occurred_at": rec.occurredAt,
                "company_industry": rec.companyIndustry,
                "company_subcategory": rec.companySubcategory,
                "company_employees": rec.companyEmployeeCount,
                "company_description": rec.companyDescription,
                "source_urls": _source_urls(rec.sources),
            },
        ),
        "",
    )


def _signal_strength(amount: int | None) -> float:
    """Bigger raise = more budget = stronger signal."""
    if amount is None:
        return 0.65
    if amount >= 100_000_000:
        return 0.90
    if amount >= 25_000_000:
        return 0.82
    if amount >= 5_000_000:
        return 0.72
    return 0.62


def _investor_names(investors: list[dict] | None) -> list[str]:
    if not investors:
        return []
    out = []
    for inv in investors:
        if isinstance(inv, dict) and inv.get("name"):
            out.append(inv["name"])
        elif isinstance(inv, str):
            out.append(inv)
    return out


def _source_urls(sources: list[dict] | None) -> list[str]:
    if not sources:
        return []
    return [s.get("url") for s in sources if isinstance(s, dict) and s.get("url")]


def _fallback_id(rec: FundingRecord, observed_at: datetime) -> str:
    company = slugify(rec.companyName or "unknown")
    rnd = slugify(rec.roundType or "round")
    return f"{company}__{rnd}::{observed_at.date().isoformat()}"


def _since_to_preset(since: datetime) -> str:
    """Map a cutoff to the smallest SignalBase date_preset that covers it.
    Coarse server hint; the occurredAt >= since check is the authority.
    """
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
    from datetime import timedelta

    from dotenv import load_dotenv

    load_dotenv(override=True)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)-7s  %(message)s")

    # Usage: python -m auto_search.connectors.funding [days] [limit] [pages]
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    pages = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    since = datetime.now(UTC) - timedelta(days=days)
    print(f"\nSignalBase funding since {since.date()} ({days}d) — "
          f"≈ {limit * pages} record-credit(s)\n")

    async def _run() -> None:
        connector = FundingConnector(max_pages=pages, per_page=limit)
        n = 0
        async for sig in connector.pull(since=since):
            n += 1
            p = sig.payload
            amt = p.get("amount_usd")
            amt_s = f"${amt:,}" if isinstance(amt, int) else "?"
            print(f"  {n:>3} {sig.company_name_raw[:30]:30} "
                  f"{str(p.get('round_type')):12} {amt_s:>14} "
                  f"{str(p.get('company_industry'))[:20]:20} s={sig.signal_strength}")
        print(f"\nDone — {n} healthcare funding signals.\n")

    asyncio.run(_run())
