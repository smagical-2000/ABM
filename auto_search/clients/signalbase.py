"""SignalBase client — real-time job-change signals via an Apify actor.

Why SignalBase
--------------
A purpose-built, real-time job-change feed (~1M tracked changes). Each record
carries `occurredAt` (exact recency), the new role, and full company context
(name, domain, industry, country, employee count). Deterministic: no news
scraping, no LLM in the detection path.

How it's called
---------------
SignalBase is wrapped by a Standby Apify actor. We invoke it through Apify's
`run-sync` endpoint with the filters as a JSON body (the same shape the Apify
console's input "JSON" tab shows). The actor proxies to SignalBase and returns
its native response: {success, data:[...], pagination:{...}, meta:{creditsUsed}}.

Server-side filters that actually work: `countries`, `seniorities`, date.
`categories`/`industry`/`subcategories` are silently ignored by the actor, so
healthcare + role filtering is done client-side in the connector.

CREDIT SAFETY
-------------
Apify bills one "api-call" event per page (≤ per_page rows), regardless of how
many match. So cost == pages fetched. The client:
  • caps pages at MAX_PAGES,
  • yields page-by-page so the connector can STOP early (it pages newest-first
    and bails once it crosses the date cutoff),
  • logs the running page/credit count.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)

_ACTOR = "signalbase~signalbase-job-changes"
_RUN_SYNC = f"https://api.apify.com/v2/acts/{_ACTOR}/run-sync"

# Hard ceiling on pages per pull, so one run can't drain the Apify balance.
MAX_PAGES = 10
DEFAULT_PER_PAGE = 50  # the actor's effective page size


class JobChangeRecord(BaseModel):
    """The subset of a SignalBase signal we use. Extra fields ignored."""

    signalId: str | None = None
    occurredAt: str | None = None           # ISO ts — the recency field
    personName: str | None = None
    personLinkedinUrl: str | None = None
    newRole: str | None = None
    postContent: str | None = None
    companyName: str | None = None
    companyWebsite: str | None = None       # domain (may be a short/vanity link)
    companyLinkedinUrl: str | None = None
    companyIndustry: str | None = None
    companyCountry: str | None = None
    companyEmployeeCount: int | None = None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> JobChangeRecord:
        known = {k: raw.get(k) for k in cls.model_fields}
        return cls(**{k: v for k, v in known.items() if v is not None})


class SignalBaseClient:
    """Pages the SignalBase job-changes feed via Apify run-sync."""

    def __init__(
        self,
        *,
        api_token: str | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = api_token or os.getenv("APIFY_API_KEY")
        if not self._token:
            raise RuntimeError("APIFY_API_KEY not set in .env")
        self._http = http  # injected for tests; otherwise we open per-call

    async def iter_job_changes(
        self,
        *,
        positions: str | None = None,
        countries: str = "US",
        seniorities: str | None = None,
        industry: str | None = None,
        date_preset: str | None = None,
        date_from: str | None = None,
        per_page: int = DEFAULT_PER_PAGE,
        max_pages: int = 3,
    ) -> AsyncIterator[JobChangeRecord]:
        """Yield job-change records newest-first, page by page.

        The caller decides when to stop consuming (e.g. once records predate
        its cutoff) — yielding lazily keeps credit spend minimal.

        `positions` is a free-text role filter (partial match on the new role)
        and is the strongest server-side narrowing we have — pass Galyna's
        target titles here so most rows are already relevant. `industry` is
        forwarded but ignored by the run-sync path today, so callers still
        filter industry locally.
        """
        pages = min(max_pages, MAX_PAGES)
        base_filters: dict[str, Any] = {
            "countries": countries,
            "limit": per_page,
            "sort_by": "occurred_at",   # API value is snake_case (not occurredAt)
            "sort_order": "desc",
        }
        if positions:
            base_filters["positions"] = positions
        if seniorities:
            base_filters["seniorities"] = seniorities
        if industry:
            base_filters["industry"] = industry
        if date_preset:
            base_filters["date_preset"] = date_preset
        if date_from:
            base_filters["dateFrom"] = date_from

        for page in range(1, pages + 1):
            body = {**base_filters, "page": page}
            data = await self._run_sync(body)
            records = data.get("data", []) or []
            credits = data.get("meta", {}).get("creditsUsed", "?")
            logger.info("signalbase page %d/%d: %d records (credits used: %s)",
                        page, pages, len(records), credits)

            if not records:
                break
            for raw in records:
                yield JobChangeRecord.from_api(raw)

            # No further pages available?
            if not data.get("pagination", {}).get("hasNextPage", False):
                break

    # ── internals ─────────────────────────────────────────────────────

    async def _run_sync(self, body: dict) -> dict:
        params = {"token": self._token}
        if self._http is not None:
            resp = await self._http.post(_RUN_SYNC, params=params, json=body)
            return resp.json()
        async with httpx.AsyncClient(timeout=120.0) as c:
            resp = await c.post(_RUN_SYNC, params=params, json=body)
            return resp.json()
