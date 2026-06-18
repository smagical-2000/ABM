"""Reply.io v3 client — READ ONLY.

Pulls email engagement + contacts for the engagement phase. Read-only by design:
only GET requests plus the `/reporting/emails` *report* query (a POST that carries
filters and returns data — it never mutates). We never create / update / delete in
Reply.io.

Auth: Bearer token from REPLYIO_API_KEY (.env, read via os.getenv — never logged).
Base: https://api.reply.io/v3. Rate limit ~100/min, so we back off on 429 (honoring
Retry-After) and on transient 5xx.

Async (httpx.AsyncClient), mirroring auto_search/clients/* — pages with top/skip
until `hasMore` is false, yielding raw rows. The ingest layer (M-C) owns mapping
those rows to our normalized shapes; this client is pure transport.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta

import httpx

logger = logging.getLogger(__name__)

V3_BASE = "https://api.reply.io/v3"

_MAX_RETRIES = 4
_BACKOFF_CAP_SECONDS = 30.0
_PAGE_CAP = 2000            # hard stop so a bad `hasMore` can't loop forever
_DEFAULT_TOP_CONTACTS = 1000
_DEFAULT_TOP_ACTIVITY = 200


class ReplyioClient:
    """Thin async, read-only client over Reply.io v3.

    Pass `http` (an httpx.AsyncClient) to inject a transport in tests; otherwise a
    client is opened per call. `api_key` defaults to REPLYIO_API_KEY.
    """

    def __init__(self, *, api_key: str | None = None,
                 http: httpx.AsyncClient | None = None, timeout: float = 60.0) -> None:
        self._key = api_key or os.getenv("REPLYIO_API_KEY")
        if not self._key:
            raise RuntimeError("REPLYIO_API_KEY not set in .env")
        self._http = http
        self._timeout = timeout

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json"}

    # ── read feeds ─────────────────────────────────────────────────────

    async def iter_contacts(self, *, top: int = _DEFAULT_TOP_CONTACTS) -> AsyncIterator[dict]:
        """Yield every contact (identity + firmographics + meeting/opt-out status).
        Paginates GET /v3/contacts via top/skip until `hasMore` is false."""
        async for row in self._paginate("GET", "/contacts",
                                        params={"top": top}, page_key="skip"):
            yield row

    async def iter_email_activity(self, *, date_from, date_to,
                                  top: int = _DEFAULT_TOP_ACTIVITY) -> AsyncIterator[dict]:
        """Yield per-contact email outcomes (delivered/opened/clicked/replied/...)
        for the window. POST /v3/reporting/emails is a report query (read-only)."""
        body = {"filters": {"from": _iso_date(date_from), "to": _iso_date(date_to)}}
        async for row in self._paginate("POST", "/reporting/emails",
                                        params={"top": top}, json=body, page_key="skip"):
            yield row

    async def contact_activities(self, contact_id, *, top: int = 100) -> list[dict]:
        """One contact's activity timeline (for the drawer). GET, read-only."""
        data = await self._request("GET", f"/contacts/{contact_id}/activities",
                                   params={"top": top, "skip": 0})
        return data.get("items") or []

    # ── transport ──────────────────────────────────────────────────────

    async def _paginate(self, method: str, path: str, *, params: dict,
                        json: dict | None = None, page_key: str) -> AsyncIterator[dict]:
        skip = 0
        for _ in range(_PAGE_CAP):
            data = await self._request(method, path, params={**params, page_key: skip},
                                       json=json)
            items = data.get("items") or []
            for it in items:
                yield it
            if not data.get("hasMore") or not items:
                return
            skip += len(items)
        logger.warning("reply.io %s %s hit the %d-page cap — results truncated",
                       method, path, _PAGE_CAP)

    async def _request(self, method: str, path: str, *, params: dict | None = None,
                       json: dict | None = None) -> dict:
        url = f"{V3_BASE}{path}"
        resp = None
        for attempt in range(1, _MAX_RETRIES + 1):
            resp = await self._send(method, url, params=params, json=json)
            if (resp.status_code == 429 or resp.status_code >= 500) and attempt < _MAX_RETRIES:
                wait = _retry_after(resp, attempt)
                logger.warning("reply.io %s %s -> %s; backoff %.2fs (try %d/%d)",
                               method, path, resp.status_code, wait, attempt, _MAX_RETRIES)
                await asyncio.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()   # exhausted retries on 429/5xx
        return {}

    async def _send(self, method: str, url: str, *, params, json) -> httpx.Response:
        if self._http is not None:
            return await self._http.request(method, url, headers=self._headers,
                                            params=params, json=json)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await client.request(method, url, headers=self._headers,
                                        params=params, json=json)


# ── helpers ────────────────────────────────────────────────────────────


def default_window(days: int = 30) -> tuple[datetime, datetime]:
    """The reporting window: from midnight UTC `days` ago, to now."""
    now = datetime.now(UTC)
    start = (now - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, now


def _iso_date(value) -> str:
    """Date-only 'YYYY-MM-DD' for the reporting window.

    Reply.io's /reporting/emails 500s on a timezone-offset datetime (e.g.
    '2026-05-15T00:00:00+00:00', which datetime.isoformat() emits); the window is
    day-granular, so we always send a bare date. See evals/bugs.json
    `replyio-reporting-500-on-offset-datetime`.
    """
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def _retry_after(resp: httpx.Response, attempt: int) -> float:
    header = resp.headers.get("Retry-After")
    if header:
        try:
            return min(float(header), _BACKOFF_CAP_SECONDS)
        except ValueError:
            pass
    return min(2.0 ** attempt, _BACKOFF_CAP_SECONDS)   # exponential, capped
