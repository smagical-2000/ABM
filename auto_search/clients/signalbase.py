"""SignalBase client — real-time GTM signals via Apify actors.

SignalBase exposes several signal feeds, each as its own Standby Apify actor:
  • job changes   — executive/leadership role changes
  • acquisitions  — M&A deals (acquirer + acquired company)
  (funding is available too; add an actor + record model when needed)

All feeds share the same transport: POST the filters as a JSON body to the
actor's Apify `run-sync` endpoint; the actor proxies to SignalBase and returns
its native response: {success, data:[...], pagination:{...}, meta:{creditsUsed}}.

Server-side filters that work (per SignalBase docs):
  • `categories`  — pipe-separated LinkedIn industry labels (company industry)
  • `countries`   — comma-separated ISO codes
  • `positions`   — free-text role match (job changes)
  • `seniorities` — enum
  • date_preset / dateFrom / dateTo
  (`industry`/`subcategories` behave differently — we use `categories`.)

CREDIT SAFETY
-------------
SignalBase bills per RECORD returned (~$20–30 / 1,000). Cost of a pull ≈
per_page × pages_fetched, so keep per_page SMALL when testing. The client:
  • caps per_page at MAX_PER_PAGE and pages at MAX_PAGES,
  • yields page-by-page so a connector can STOP early at its date cutoff,
  • logs rows pulled per page so spend is visible.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx
from pydantic import BaseModel, field_validator

logger = logging.getLogger(__name__)

# One actor per signal feed.
JOB_CHANGES_ACTOR = "signalbase~signalbase-job-changes"
ACQUISITIONS_ACTOR = "signalbase~signalbase-acquisitions"

_RUN_SYNC = "https://api.apify.com/v2/acts/{actor}/run-sync"

# Hard ceilings so one run can't drain the balance. SignalBase bills per
# record, so per_page is the real cost knob — cap it low.
MAX_PAGES = 10
MAX_PER_PAGE = 50
DEFAULT_PER_PAGE = 5  # cost-safe default (records per call = credits)


# ── typed records ─────────────────────────────────────────────────────


class _BaseRecord(BaseModel):
    """Shared helper: build from a raw dict, ignoring unknown fields."""

    @classmethod
    def from_api(cls, raw: dict[str, Any]):
        known = {k: raw.get(k) for k in cls.model_fields}
        return cls(**{k: v for k, v in known.items() if v is not None})


class JobChangeRecord(_BaseRecord):
    """A SignalBase job-change signal (subset we use)."""

    signalId: str | None = None
    occurredAt: str | None = None           # ISO ts — recency
    personName: str | None = None
    personLinkedinUrl: str | None = None
    newRole: str | None = None
    postContent: str | None = None
    companyName: str | None = None
    companyWebsite: str | None = None
    companyLinkedinUrl: str | None = None
    companyIndustry: str | None = None
    companySubcategory: str | None = None
    companyCountry: str | None = None
    companyEmployeeCount: int | None = None


class AcquisitionRecord(_BaseRecord):
    """A SignalBase acquisition signal (subset we use).

    The PRIMARY company fields (companyName, …) describe the ACQUIRED company —
    the one "in transition", which is the buying signal we care about. The
    acquiringCompany* fields describe the buyer (kept for context).
    """

    signalId: str | None = None
    occurredAt: str | None = None           # ISO ts — recency
    announcedDate: str | None = None
    # acquired (target) company — the signal subject
    companyName: str | None = None
    companyWebsite: str | None = None
    companyLinkedin: str | None = None
    companyIndustry: str | None = None
    companySubcategory: str | None = None
    companyCountry: str | None = None
    companyEmployeeCount: int | None = None
    companyDescription: str | None = None
    # acquiring company — context
    acquiringCompanyName: str | None = None
    acquiringCompanyWebsite: str | None = None
    acquiringCompanyIndustry: str | None = None
    acquiringCompanyCountry: str | None = None
    # deal
    amount: int | None = None
    currency: str | None = None
    # Free text in the wild: a number ("100"), a percent, or "Majority" /
    # "Minority". Keep it as a string and never let it break parsing.
    percentage: str | None = None
    sources: list[dict] | None = None

    @field_validator("percentage", mode="before")
    @classmethod
    def _percent_to_str(cls, v: object) -> str | None:
        return None if v is None else str(v)


# ── client ────────────────────────────────────────────────────────────


class SignalBaseClient:
    """Pages any SignalBase feed via Apify run-sync. Actor-agnostic."""

    def __init__(
        self,
        *,
        api_token: str | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = api_token or os.getenv("APIFY_API_KEY")
        if not self._token:
            raise RuntimeError("APIFY_API_KEY not set in .env")
        self._http = http  # injected for tests; otherwise opened per-call

    # ── typed feeds ───────────────────────────────────────────────────

    async def iter_job_changes(
        self,
        *,
        positions: str | None = None,
        countries: str = "US",
        seniorities: str | None = None,
        categories: str | None = None,
        date_preset: str | None = None,
        per_page: int = DEFAULT_PER_PAGE,
        max_pages: int = 1,
    ) -> AsyncIterator[JobChangeRecord]:
        """Yield job-change records newest-first.

        `positions` (free-text role match) is the strongest narrowing;
        `categories` filters by company industry server-side.
        """
        filters = _compact({
            "positions": positions,
            "countries": countries,
            "seniorities": seniorities,
            "categories": categories,
            "date_preset": date_preset,
        })
        async for raw in self._iter_raw(
            JOB_CHANGES_ACTOR, filters, per_page=per_page, max_pages=max_pages
        ):
            rec = _parse_or_skip(JobChangeRecord, raw)
            if rec is not None:
                yield rec

    async def iter_acquisitions(
        self,
        *,
        categories: str | None = None,
        countries: str = "US",
        date_preset: str | None = None,
        per_page: int = DEFAULT_PER_PAGE,
        max_pages: int = 1,
    ) -> AsyncIterator[AcquisitionRecord]:
        """Yield acquisition records newest-first.

        `categories` filters by the ACQUIRED company's industry server-side.
        """
        filters = _compact({
            "categories": categories,
            "countries": countries,
            "date_preset": date_preset,
        })
        async for raw in self._iter_raw(
            ACQUISITIONS_ACTOR, filters, per_page=per_page, max_pages=max_pages
        ):
            rec = _parse_or_skip(AcquisitionRecord, raw)
            if rec is not None:
                yield rec

    # ── generic transport ─────────────────────────────────────────────

    async def _iter_raw(
        self,
        actor: str,
        filters: dict[str, Any],
        *,
        per_page: int,
        max_pages: int,
    ) -> AsyncIterator[dict]:
        """Page an actor's feed, yielding raw record dicts newest-first.

        Stops at the last available page. Callers stop earlier (at their date
        cutoff) by simply not consuming further — every record yielded after
        the cutoff still costs credits, so connectors break promptly.
        """
        pages = min(max_pages, MAX_PAGES)
        per_page = max(1, min(per_page, MAX_PER_PAGE))
        base = {
            **filters,
            "limit": per_page,
            "sort_by": "occurred_at",   # snake_case API value (not occurredAt)
            "sort_order": "desc",
        }

        for page in range(1, pages + 1):
            data = await self._run_sync(actor, {**base, "page": page})
            records = data.get("data", []) or []
            credits = data.get("meta", {}).get("creditsUsed", "?")
            logger.info(
                "signalbase[%s] page %d/%d: %d records (credits: %s)",
                actor.split("~")[-1], page, pages, len(records), credits,
            )
            if not records:
                break
            for raw in records:
                yield raw
            if not data.get("pagination", {}).get("hasNextPage", False):
                break

    async def _run_sync(self, actor: str, body: dict) -> dict:
        url = _RUN_SYNC.format(actor=actor)
        params = {"token": self._token}
        if self._http is not None:
            resp = await self._http.post(url, params=params, json=body)
            return resp.json()
        async with httpx.AsyncClient(timeout=120.0) as c:
            resp = await c.post(url, params=params, json=body)
            return resp.json()


def _compact(d: dict[str, Any]) -> dict[str, Any]:
    """Drop None-valued keys so we only send filters that are set."""
    return {k: v for k, v in d.items() if v is not None}


def _parse_or_skip(model_cls, raw: dict):
    """Build a record, or log+skip on a validation error.

    One malformed row (e.g. an unexpected field type from the upstream API)
    must never crash a whole paid pull — we already spent the credits.
    """
    try:
        return model_cls.from_api(raw)
    except Exception as e:  # noqa: BLE001 — defensive at the API boundary
        logger.warning(
            "skipping unparseable %s record (%s): %.120s",
            model_cls.__name__, e, raw,
        )
        return None
