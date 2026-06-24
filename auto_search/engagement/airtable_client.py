"""Airtable client — the ONE write the LinkedIn TOFU flow makes.

`upsert` creates-or-updates a row in a base/table, merging on a key field (Email)
so an hourly re-run of the same person updates the row instead of duplicating it.
Mirrors the Reply.io client's transport: async httpx, Bearer auth, retry/backoff on
429 / 5xx (honoring Retry-After). No reads — this flow only pushes.

Base + table default to AIRTABLE_BASE_ID / AIRTABLE_LINKEDIN_TABLE; the token to
AIRTABLE_API_KEY (a Personal Access Token with data.records:write). Use the table
ID (tbl...) — it needs no URL-encoding, unlike the human table name.
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://api.airtable.com/v0"
_MAX_RETRIES = 4
_BACKOFF_CAP_SECONDS = 30.0
_BATCH_CAP = 10            # Airtable accepts at most 10 records per write


class AirtableClient:
    """Thin async Airtable write client.

    Pass `http` (an httpx.AsyncClient) to inject a transport in tests; otherwise a
    client is opened per call. `base_id` / `table` default to env; `api_key` to
    AIRTABLE_API_KEY.
    """

    def __init__(self, *, base_id: str | None = None, table: str | None = None,
                 api_key: str | None = None, http: httpx.AsyncClient | None = None,
                 timeout: float = 30.0) -> None:
        self._base = base_id or os.getenv("AIRTABLE_BASE_ID")
        self._table = table or os.getenv("AIRTABLE_LINKEDIN_TABLE")
        self._key = api_key or os.getenv("AIRTABLE_API_KEY")
        if not (self._base and self._table):
            raise RuntimeError("AIRTABLE_BASE_ID / AIRTABLE_LINKEDIN_TABLE not set")
        if not self._key:
            raise RuntimeError("AIRTABLE_API_KEY not set in .env")
        self._http = http
        self._timeout = timeout

    @property
    def _url(self) -> str:
        return f"{_BASE}/{self._base}/{self._table}"

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json"}

    async def upsert(self, fields: dict, *, merge_on: list[str]) -> dict:
        """Create-or-update one row, merging on `merge_on` (e.g. ["Email"]). WRITE.

        PATCH the table with `performUpsert.fieldsToMergeOn` — Airtable matches on
        those fields and updates the row if found, else creates it. `typecast` lets
        Airtable coerce string values into the column types. Returns the API json
        (records carry their `id` + whether they were created/updated)."""
        body = {
            "performUpsert": {"fieldsToMergeOn": list(merge_on)},
            "records": [{"fields": fields}],
            "typecast": True,
        }
        return await self._send("PATCH", body)

    async def create(self, fields: dict) -> dict:
        """Create one row unconditionally (no merge key, e.g. an emailless row)."""
        body = {"records": [{"fields": fields}], "typecast": True}
        return await self._send("POST", body)

    @staticmethod
    def record_id(resp: dict) -> str | None:
        """Pull the (first) record id out of an upsert/create response."""
        recs = (resp or {}).get("records") or []
        return recs[0].get("id") if recs else None

    async def _send(self, method: str, body: dict) -> dict:
        resp = None
        for attempt in range(1, _MAX_RETRIES + 1):
            if self._http is not None:
                resp = await self._http.request(method, self._url, headers=self._headers,
                                                json=body)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.request(method, self._url, headers=self._headers,
                                                json=body)
            if (resp.status_code == 429 or resp.status_code >= 500) and attempt < _MAX_RETRIES:
                wait = _retry_after(resp, attempt)
                logger.warning("airtable %s -> %s; backoff %.2fs (try %d/%d)",
                               method, resp.status_code, wait, attempt, _MAX_RETRIES)
                await asyncio.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()   # exhausted retries on 429/5xx
        return {}


def _retry_after(resp: httpx.Response, attempt: int) -> float:
    header = resp.headers.get("Retry-After")
    if header:
        try:
            return min(float(header), _BACKOFF_CAP_SECONDS)
        except ValueError:
            pass
    return min(2.0 ** attempt, _BACKOFF_CAP_SECONDS)   # exponential, capped
