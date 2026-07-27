"""WARN-notice connector — warntracker.com.

What this is
------------
WARN (Worker Adjustment and Retraining Notification) notices are layoff
filings that US employers with 100+ staff are legally required to submit
before a mass layoff. Because they're a legal obligation rather than
voluntary PR, the data is more complete and less noisy than self-reported
trackers like layoffs.fyi.

How we get the data
-------------------
warntracker.com renders its table client-side: the browser calls
`/api/sample_warn_listings` after page load, and that endpoint only answers
requests carrying the session cookies set during that load. A plain HTTP GET
returns 404. So we drive a headless browser (Playwright):

    1. Navigate to the homepage (sets cookies, fires the XHR)
    2. Intercept the /api/sample_warn_listings response
    3. Read the JSON straight off the wire — no HTML parsing needed

Results are cached to disk so tests and offline runs don't need a browser
(set WARN_USE_CACHE=true).

Source field schema (observed May 2026):
    "Company Name", "# Laid off", "Layoff date", "Notice Date",
    "State", "Year", "companyId", "📍 City/Jurisdiction"

Env
---
    (none required — the site is public)
    WARN_USE_CACHE=true            read the cached JSON instead of scraping
    WARN_CACHE_PATH=./data/...     cache location (default ./data/warn_cache.json)

STATUS: UPSTREAM DEAD (verified 2026-07-27)
-------------------------------------------
warntracker.com itself stopped updating around 2026-04-27. Loading the site
fresh and reading its OWN /api/sample_warn_listings response returns 200 rows
whose newest Notice Date is 2026-04-27 — the identical frozen snapshot we get;
it is the only data API the homepage calls, and the state pages 404. WARN
filings are statutory and continuous, so a three-month-old newest notice means
the SITE is dead, not the market. Our last new company from this source was
2026-06-09.

So the stale-feed tripwire below fires on EVERY run, by design and correctly.
Do not "fix" it by widening _STALE_FEED_DAYS or by retrying — this source needs
REPLACING (state WARN portal aggregation, DOL data, or layoffs.fyi).
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from auto_search.models import MIN_LAID_OFF, RawSignal
from auto_search.normalize import parse_int_loose, slugify

logger = logging.getLogger(__name__)

_WARN_API_PATH = "/api/sample_warn_listings"
_WARN_BASE_URL = "https://www.warntracker.com"
_PAGE_TIMEOUT_MS = 20_000

# Freshness tripwire (2026-07-23 audit): the sample_warn_listings endpoint
# served a FROZEN April snapshot through Jun–Jul 2026 — every daily run
# "succeeded" on a corpse (rows fetched, all dropped as before_window) and
# nobody noticed for weeks. WARN filings arrive continuously nationwide, so a
# newest notice older than this many days means the FEED is dead, not quiet.
_STALE_FEED_DAYS = 30

# Field-name aliases — the site occasionally renames columns, so we look up
# each logical field through a list of candidates rather than one hard key.
_F_COMPANY = ("Company Name", "company", "Company")
_F_LAID_OFF = ("# Laid off", "# Laid Off", "numLayoffs", "laid_off")
_F_LAYOFF_DATE = ("Layoff date", "Layoff Date", "layoffDate")
_F_NOTICE_DATE = ("Notice Date", "noticeDate")
_F_STATE = ("State", "state")
_F_YEAR = ("Year", "year")
_F_COMPANY_ID = ("companyId", "company_id")
_F_CITY = ("📍 City/Jurisdiction", "City/Jurisdiction", "city")
_F_COMPANY_URL = ("_warntracker.com_link_for_company_view",)


class WarnTrackerConnector:
    """Pull layoff signals from warntracker.com WARN notices.

    Implements the SignalConnector protocol (see connectors/base.py):
    one `pull(since)` method yielding RawSignal objects. The pipeline that
    consumes these doesn't know or care that the source is warntracker.
    """

    source_name = "warntracker"
    signal_types = ["layoff"]
    default_cron = "0 6 * * *"  # 06:00 UTC daily

    def __init__(self) -> None:
        self._use_cache = os.getenv("WARN_USE_CACHE", "").lower() in ("1", "true")
        self._cache_path = Path(os.getenv("WARN_CACHE_PATH", "./data/warn_cache.json"))

    # ── public API ────────────────────────────────────────────────────

    async def pull(self, since: datetime) -> AsyncIterator[RawSignal]:
        """Yield layoff signals with a layoff/notice date on or after `since`.

        Drops are counted by reason and logged so a "0 results" run is
        immediately diagnosable (wrong date window? all below threshold?).
        """
        rows = await self._fetch_rows()
        logger.info("warntracker returned %d total rows", len(rows))

        # Fail LOUDLY on a frozen feed instead of succeeding on a corpse. The
        # chain this comment used to claim did not actually exist until
        # 2026-07-27: run_discovery's 1-of-N policy caught the exception,
        # printed a warning and exited 0, so run_daily said "all legs OK" and
        # the only loud path was run_digest's throttled 24h source-silence
        # WARNING, lumped in with every other quiet source. It is real now —
        # a raised error marks the connector_runs row FAILED and run_discovery
        # posts one consolidated FAILURE-severity ops alert naming this source
        # (see scripts/run_discovery.py alert_failed_sources). A quiet 0-yield
        # run still tells nobody anything, which is exactly why we raise.
        newest = _newest_notice_date(rows)
        if rows and newest is not None and \
                newest < datetime.now(UTC) - timedelta(days=_STALE_FEED_DAYS):
            raise RuntimeError(
                f"warntracker feed stale: newest notice {newest.date().isoformat()} "
                "— endpoint serving frozen sample. warntracker.com stopped updating "
                "~2026-04-27 (verified 2026-07-27): REPLACE the source, do not "
                "retry or widen the window.")

        drops: Counter[str] = Counter()
        yielded = 0

        for row in rows:
            signal, drop_reason = self._row_to_signal(row, since)
            if signal is None:
                drops[drop_reason] += 1
                logger.debug(
                    "drop[%s] company=%r state=%r laid_off=%r date=%r",
                    drop_reason,
                    _first(row, _F_COMPANY),
                    _first(row, _F_STATE),
                    _first(row, _F_LAID_OFF),
                    _first(row, _F_LAYOFF_DATE),
                )
                continue
            yielded += 1
            yield signal

        logger.info("warntracker pull done — total=%d yielded=%d", len(rows), yielded)
        for reason, n in drops.most_common():
            logger.info("  dropped %4d  %s", n, reason)

    # ── data acquisition ──────────────────────────────────────────────

    async def _fetch_rows(self) -> list[dict[str, Any]]:
        """Return raw WARN rows — from cache if requested, else via browser."""
        if self._use_cache:
            return self._read_cache()
        return await self._scrape_rows()

    def _read_cache(self) -> list[dict[str, Any]]:
        if not self._cache_path.exists():
            raise FileNotFoundError(
                f"WARN cache not found at {self._cache_path}. "
                "Run once with WARN_USE_CACHE unset to populate it."
            )
        logger.info("reading cached WARN rows from %s", self._cache_path)
        return json.loads(self._cache_path.read_text())

    async def _scrape_rows(self) -> list[dict[str, Any]]:
        """Drive a headless browser to capture the WARN data API response.

        `page.expect_response(...)` is a context manager: we open it BEFORE
        navigating so the listener is armed when the XHR fires, then read
        `.value` (the Response) once inside the block. goto() may raise on
        slow loads even though the XHR already fired — that's non-fatal.
        """
        from playwright.async_api import async_playwright

        logger.info("launching headless browser to scrape warntracker…")
        rows: list[dict[str, Any]] = []

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                async with page.expect_response(
                    lambda r: _WARN_API_PATH in r.url and r.status == 200,
                    timeout=_PAGE_TIMEOUT_MS,
                ) as response_info:
                    try:
                        await page.goto(
                            _WARN_BASE_URL,
                            wait_until="domcontentloaded",
                            timeout=_PAGE_TIMEOUT_MS,
                        )
                    except Exception as nav_err:  # noqa: BLE001
                        logger.debug("goto raised (non-fatal): %s", nav_err)

                response = await response_info.value
                rows = await response.json()
                logger.info("intercepted %d rows from %s", len(rows), _WARN_API_PATH)
            except Exception as err:  # noqa: BLE001 — surface, don't crash
                logger.error(
                    "failed to intercept WARN API: %s — re-run, or set "
                    "WARN_USE_CACHE=true once data/warn_cache.json exists",
                    err,
                )
            finally:
                await browser.close()

        if rows:
            self._write_cache(rows)
        return rows

    def _write_cache(self, rows: list[dict[str, Any]]) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(json.dumps(rows, indent=2, default=str))
        logger.info("cached %d rows to %s", len(rows), self._cache_path)

    # ── row → signal ──────────────────────────────────────────────────

    def _row_to_signal(
        self, row: dict[str, Any], since: datetime
    ) -> tuple[RawSignal | None, str]:
        """Map one WARN row to a RawSignal, or (None, reason) if filtered out.

        Filters applied here are STRUCTURAL only (presence, date window,
        minimum scale). ICP classification is the qualifier's job — we do
        not guess healthcare-vs-not from the row.
        """
        company = (_first(row, _F_COMPANY) or "").strip()
        if not company:
            return None, "missing_company"

        # Prefer the actual layoff date; fall back to the notice date.
        observed_at = _parse_date(
            _first(row, _F_LAYOFF_DATE) or _first(row, _F_NOTICE_DATE) or ""
        )
        if observed_at is None:
            return None, "unparseable_date"
        if observed_at < since:
            return None, "before_window"

        laid_off = parse_int_loose(_first(row, _F_LAID_OFF))
        if laid_off is not None and laid_off < MIN_LAID_OFF:
            return None, "below_min_laid_off"

        # No geo filter: WARN notices are US-only by statute.
        state = (_first(row, _F_STATE) or "").upper().strip()
        city = _first(row, _F_CITY) or ""

        return (
            RawSignal(
                source=self.source_name,
                source_external_id=_external_id(
                    company=company,
                    observed_at=observed_at,
                    state=state,
                    city=city,
                    company_id=_first(row, _F_COMPANY_ID),
                ),
                signal_type="layoff",
                company_name_raw=company,
                company_domain_raw=None,  # qualifier discovers the domain
                observed_at=observed_at,
                signal_strength=_signal_strength(laid_off),
                payload={
                    "laid_off_count": laid_off,
                    "state": state,
                    "city": city,
                    "notice_date": _first(row, _F_NOTICE_DATE),
                    "layoff_date": _first(row, _F_LAYOFF_DATE),
                    "year": _first(row, _F_YEAR),
                    "company_id": _first(row, _F_COMPANY_ID),
                    "company_url": _first(row, _F_COMPANY_URL),
                },
            ),
            "",
        )


# ── module-level helpers ──────────────────────────────────────────────


def _first(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    """Return the first present, non-None value among candidate keys."""
    for k in keys:
        if k in row and row[k] is not None:
            return row[k]
    return None


def _newest_notice_date(rows: list[dict[str, Any]]) -> datetime | None:
    """Newest notice date across the payload — the feed-freshness measure.

    Notice date first (it's the filing timestamp, i.e. when the FEED learned
    of the event); the layoff date only as a per-row fallback. Unparseable
    rows are skipped — None means "no parseable dates at all", which the
    caller treats as not-provably-stale (the drop counters surface that case).
    """
    newest: datetime | None = None
    for row in rows:
        raw = _first(row, _F_NOTICE_DATE) or _first(row, _F_LAYOFF_DATE)
        d = _parse_date(str(raw)) if raw is not None else None
        if d is not None and (newest is None or d > newest):
            newest = d
    return newest


def _external_id(
    *,
    company: str,
    observed_at: datetime,
    state: str | None,
    city: str | None,
    company_id: str | None,
) -> str:
    """Stable per-EVENT dedup key — must be unique per distinct WARN filing.

    A company can file multiple WARN notices on the same date for different
    sites (e.g. two plants in two cities). Keying on company+date alone would
    collapse them and silently drop the second one. So we include location
    and the source's own companyId to keep distinct filings distinct, while
    staying stable across re-runs (same filing → same id → safe to re-ingest).

    Company-LEVEL dedup (one Claude call per company) is a separate concern,
    enforced via RawSignal.company_key / normalize_company_name().
    """
    parts = [
        slugify(company),
        (company_id or "").strip().lower(),
        (state or "").strip().lower(),
        slugify(city or ""),
        observed_at.date().isoformat(),
    ]
    return "::".join(parts)


def _parse_date(s: str) -> datetime | None:
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _signal_strength(laid_off: int | None) -> float:
    """Soft prior used to sort the review queue. The qualifier's website
    research is the real signal — this just floats bigger layoffs up.
    WARN filings come from 100+ employee companies, so the floor is high.
    """
    if laid_off is None:
        return 0.55
    if laid_off >= 500:
        return 0.85
    if laid_off >= 200:
        return 0.75
    if laid_off >= 50:
        return 0.65
    return 0.55


# ── manual CLI trigger ────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    import sys
    from datetime import timedelta

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
    )

    days = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    since = datetime.now(UTC) - timedelta(days=days)
    print(f"\nFetching WARN notices since {since.date()} ({days}d back)\n")

    async def _run() -> None:
        connector = WarnTrackerConnector()
        count = 0
        async for sig in connector.pull(since=since):
            count += 1
            print(
                f"  {count:>3}  {sig.company_name_raw:<40}"
                f"  {sig.payload.get('state', '??'):<4}"
                f"  laid_off={str(sig.payload.get('laid_off_count') or '?'):<6}"
                f"  s={sig.signal_strength:.2f}"
            )
        print(f"\nDone — {count} WARN signals.\n")

    asyncio.run(_run())
