"""Job-board scrapers via Apify — Indeed (primary) + LinkedIn (secondary).

Unlike SignalBase (Standby actors proxied over `run-sync`), these are regular
Apify actors: we POST the input and use `run-sync-get-dataset-items`, which
starts the run, waits for it, and returns the dataset rows directly as a JSON
array. One call per title query.

Why these two:
    SignalBase's hiring feed has no job-title filter, so it can't answer
    "who's hiring a *medical coder*". Indeed and LinkedIn both expose a quoted
    title search, which is exactly the lever we need to find US healthcare
    providers staffing up revenue-cycle roles.

CREDIT SAFETY
-------------
Both actors bill per result row. Cost of a search ≈ max_rows. Keep it small
(default 10) and search only the essential RCM titles — see job_postings.py.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)

INDEED_ACTOR = "borderline~indeed-scraper"
LINKEDIN_ACTOR = "valig~linkedin-jobs-scraper"

_RUN_ITEMS = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"

# Hard ceiling so one search can't run away with the bill.
MAX_ROWS = 50
DEFAULT_ROWS = 10

# Indeed `fromDays` is an enum; LinkedIn `datePosted` is a coded preset.
_INDEED_FROM_DAYS = ("1", "3", "7", "14")
_LINKEDIN_DATE = {1: "r86400", 7: "r604800", 30: "r2592000"}  # 24h / week / month


# ── typed records ─────────────────────────────────────────────────────


class _BaseJob(BaseModel):
    """Build from a raw dict, ignoring unknown fields (schemas drift)."""

    @classmethod
    def from_api(cls, raw: dict[str, Any]):
        known = {k: raw.get(k) for k in cls.model_fields}
        return cls(**{k: v for k, v in known.items() if v is not None})


class IndeedJob(_BaseJob):
    jobKey: str | None = None
    title: str | None = None
    companyName: str | None = None
    companyUrl: str | None = None          # Indeed cmp page (not the real site)
    companyIndustry: str | None = None     # usually null — don't rely on it
    companyNumEmployees: str | None = None
    companyLinks: dict[str, Any] | None = None  # holds corporateWebsite
    location: dict[str, Any] | None = None      # city/state/countryCode/…
    datePublished: str | None = None       # 'YYYY-MM-DD'
    age: str | None = None                 # '5 hours ago'
    postedToday: bool | None = None
    jobType: list[str] | None = None
    salary: dict[str, Any] | None = None
    jobUrl: str | None = None
    applyUrl: str | None = None
    isRemote: str | bool | None = None
    emails: list[str] | None = None
    expired: bool | None = None


class LinkedInJob(_BaseJob):
    id: str | None = None
    url: str | None = None
    title: str | None = None
    companyName: str | None = None
    companyUrl: str | None = None
    location: str | None = None
    experienceLevel: str | None = None
    contractType: str | None = None
    workType: str | None = None
    sector: str | None = None
    salary: str | None = None
    postedDate: str | None = None          # 'YYYY-MM-DD'
    postedTimeAgo: str | None = None
    applicationsCount: str | None = None


# ── client ────────────────────────────────────────────────────────────


class ApifyJobsClient:
    """Thin async client over the Indeed + LinkedIn Apify scrapers."""

    def __init__(
        self,
        token: str | None = None,
        *,
        http: httpx.AsyncClient | None = None,
        timeout: float = 300.0,
    ) -> None:
        self._token = token or os.environ.get("APIFY_API_KEY", "")
        if not self._token:
            raise RuntimeError("APIFY_API_KEY not set")
        self._http = http
        self._timeout = timeout

    async def search_indeed(
        self,
        query: str,
        *,
        country: str = "us",
        from_days: str = "1",
        max_rows: int = DEFAULT_ROWS,
        sort: str = "date",
        location: str | None = None,
        remote: str | None = None,
    ) -> list[IndeedJob]:
        """Run the Indeed actor for one title query. `query` is passed verbatim
        (quote it — '"medical coder"' — for an exact-phrase title match).
        """
        if from_days not in _INDEED_FROM_DAYS:
            from_days = "1"
        body = _compact({
            "country": country,
            "query": query,
            "location": location,
            "remote": remote,
            "sort": sort,
            "fromDays": from_days,
            "maxRows": _cap_rows(max_rows),
        })
        rows = await self._run(INDEED_ACTOR, body)
        return _parse_all(IndeedJob, rows)

    async def search_linkedin(
        self,
        title: str,
        *,
        location: str = "United States",
        days: int = 1,
        limit: int = DEFAULT_ROWS,
    ) -> list[LinkedInJob]:
        """Run the LinkedIn actor for one title query (secondary source)."""
        body = _compact({
            "title": title,
            "location": location,
            "datePosted": _LINKEDIN_DATE.get(days, "r86400"),
            "limit": _cap_rows(limit),
        })
        rows = await self._run(LINKEDIN_ACTOR, body)
        return _parse_all(LinkedInJob, rows)

    # ── transport ─────────────────────────────────────────────────────

    async def _run(self, actor: str, body: dict) -> list[dict]:
        """POST input, wait for the run, return dataset rows (a JSON array)."""
        url = _RUN_ITEMS.format(actor=actor)
        params = {"token": self._token}
        rows = await self._post_json(url, params, body)
        if not isinstance(rows, list):
            logger.warning("apify[%s] unexpected non-list response: %.180s",
                           actor.split("~")[-1], rows)
            return []
        logger.info("apify[%s] query=%r → %d rows",
                    actor.split("~")[-1], body.get("query") or body.get("title"),
                    len(rows))
        return rows

    async def _post_json(self, url: str, params: dict, body: dict) -> Any:
        if self._http is not None:
            resp = await self._http.post(url, params=params, json=body)
            return resp.json()
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            resp = await c.post(url, params=params, json=body)
            return resp.json()


# ── helpers ───────────────────────────────────────────────────────────


def _cap_rows(n: int) -> int:
    return max(1, min(int(n), MAX_ROWS))


def _compact(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def _parse_all(model_cls, rows: list[dict]) -> list:
    out = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        try:
            out.append(model_cls.from_api(raw))
        except Exception as e:  # noqa: BLE001 — one bad row mustn't drop the page
            logger.warning("skipping unparseable %s row (%s)", model_cls.__name__, e)
    return out
