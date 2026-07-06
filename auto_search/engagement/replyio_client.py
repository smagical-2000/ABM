"""Reply.io client — read feeds + one deliberate write.

Pulls email engagement + contacts for the engagement phase: GET requests plus the
`/reporting/emails` *report* query (a POST that returns data, never mutates). The ONE
write is `add_to_campaign` — used only by the LinkedIn TOFU ad-engagement flow to
create a contact and push it into the matching engagement campaign (replicating the
old Clay step); no sync calls it.

Auth: the same REPLYIO_API_KEY backs both surfaces — Bearer on v3 (the read feeds),
`x-api-key` on v1 (where campaigns + the add-and-push action live). Base v3:
https://api.reply.io/v3; v1: https://api.reply.io/v1. Rate limit ~100/min, so we back
off on 429 (honoring Retry-After) and on transient 5xx.

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
V1_BASE = "https://api.reply.io/v1"   # campaigns + the add-and-push-to-campaign write

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

    async def list_campaigns(self) -> list[dict]:
        """Every campaign (id, name, status) — read-only, for the Campaigns tab's
        sequence-mapping picker. Lives on the v1 surface (x-api-key), same as the
        add-and-push write; the v3 API has no campaigns list. Same 429/5xx backoff."""
        url = f"{V1_BASE}/campaigns"
        headers = {"x-api-key": self._key, "Content-Type": "application/json"}
        resp = None
        for attempt in range(1, _MAX_RETRIES + 1):
            if self._http is not None:
                resp = await self._http.request("GET", url, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.request("GET", url, headers=headers)
            if (resp.status_code == 429 or resp.status_code >= 500) and attempt < _MAX_RETRIES:
                await asyncio.sleep(_retry_after(resp, attempt))
                continue
            resp.raise_for_status()
            data = resp.json()
            rows = data if isinstance(data, list) else data.get("items") or []
            return [{"id": r.get("id"), "name": r.get("name"), "status": r.get("status")}
                    for r in rows if isinstance(r, dict)]
        resp.raise_for_status()   # exhausted retries on 429/5xx
        return []

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

    # ── write (LinkedIn TOFU flow only) ─────────────────────────────────

    async def remove_from_campaign(self, *, campaign_id: int, email: str) -> bool:
        """Pull a contact out of a campaign — the cross-channel stop rule's email
        lever (a LinkedIn reply pauses the email drip). WRITE (v1, x-api-key),
        same actions family as add_to_campaign. Best-effort by contract: True on
        2xx, False (logged) on anything else — a stop sweep must never raise.
        NOTE: endpoint from Reply.io's v1 actions family; verify on first live
        stop (a 404 here means the path needs the by-id variant instead)."""
        url = (f"{V1_BASE}/actions/removepersonfromcampaignbyemail"
               f"?email={(email or '').strip()}&campaignId={int(campaign_id)}")
        headers = {"x-api-key": self._key}
        try:
            if self._http is not None:
                resp = await self._http.request("POST", url, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.request("POST", url, headers=headers)
            if 200 <= resp.status_code < 300:
                return True
            logger.warning("reply.io remove_from_campaign %s -> %s: %s",
                           email, resp.status_code, resp.text[:120])
            return False
        except httpx.HTTPError as e:
            logger.warning("reply.io remove_from_campaign failed for %s: %s", email, e)
            return False

    async def add_to_campaign(self, *, campaign_id: int, email: str,
                              first_name: str | None = None, last_name: str | None = None,
                              company: str | None = None, title: str | None = None,
                              phone: str | None = None) -> dict:
        """Create a contact and push it into a campaign. WRITE (v1, x-api-key).

        Reply.io v1 `POST /actions/addandpushtocampaign` — the same action Clay used.
        Idempotent on Reply.io's side: re-adding an existing email updates the contact
        rather than duplicating it. Backs off on 429 / 5xx like the read path."""
        body = {"campaignId": int(campaign_id), "email": (email or "").strip()}
        for k, v in (("firstName", first_name), ("lastName", last_name),
                     ("company", company), ("title", title), ("phone", phone)):
            if v and str(v).strip():
                body[k] = str(v).strip()
        url = f"{V1_BASE}/actions/addandpushtocampaign"
        headers = {"x-api-key": self._key, "Content-Type": "application/json"}
        resp = None
        for attempt in range(1, _MAX_RETRIES + 1):
            if self._http is not None:
                resp = await self._http.request("POST", url, headers=headers, json=body)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.request("POST", url, headers=headers, json=body)
            if (resp.status_code == 429 or resp.status_code >= 500) and attempt < _MAX_RETRIES:
                await asyncio.sleep(_retry_after(resp, attempt))
                continue
            if resp.status_code == 409:
                # Reply.io dedups by email: the contact already exists / is in another
                # sequence. Expected, not an error — the person is already in outreach.
                return {"status": 409, "detail": resp.text[:200]}
            resp.raise_for_status()
            try:
                return resp.json()
            except ValueError:
                return {"status": resp.status_code, "body": resp.text[:200]}
        resp.raise_for_status()
        return {}


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
