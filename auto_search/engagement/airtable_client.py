"""Airtable client — the ONE write the LinkedIn TOFU flow makes.

`upsert` creates-or-updates a row in a base/table, matching on a key field (Email)
so a re-run of the same person updates the row instead of duplicating it. It looks
the row up first and updates the first match (or creates one) — dup-tolerant, because
another tool (e.g. a Clay workflow) may have already left duplicate rows that would
break Airtable's native merge-on-key upsert (422). Mirrors the Reply.io client's
transport: async httpx, Bearer auth, retry/backoff on 429 / 5xx (honoring Retry-After).

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
        """Create-or-update one row, matching on `merge_on` (e.g. ["Email"]). WRITE.

        Dup-tolerant: looks the row up by the merge fields and UPDATES the first match
        (PATCH by record id), else CREATES one. We deliberately do NOT use Airtable's
        native `performUpsert` — it returns 422 when MORE THAN ONE existing row matches
        the merge key, which happens whenever another tool (e.g. a Clay workflow) also
        writes this table. Finding + updating the first match never 422s and never adds
        another duplicate. `typecast` coerces strings into the column types."""
        rec_id = await self._find_id(fields, merge_on)
        if rec_id:
            return await self._send("PATCH",
                                    {"records": [{"id": rec_id, "fields": fields}],
                                     "typecast": True})
        return await self._send("POST", {"records": [{"fields": fields}], "typecast": True})

    async def _find_id(self, fields: dict, merge_on: list[str]) -> str | None:
        """First record id matching ALL merge fields, or None. Read-only."""
        import urllib.parse

        def lit(v: object) -> str:
            # Airtable formula string literal: wrap in DOUBLE quotes, escaping only \ and ".
            # An apostrophe is literal inside a double-quoted string (Airtable does NOT honor
            # \' inside a single-quoted literal), so a value like "o'brien@x.com" matches
            # correctly instead of producing a malformed formula → a missed match → a dup.
            s = str(v).replace("\\", "\\\\").replace('"', '\\"')
            return f'"{s}"'

        clauses = [f"{{{k}}}={lit(fields.get(k, ''))}" for k in merge_on]
        formula = clauses[0] if len(clauses) == 1 else "AND(" + ",".join(clauses) + ")"
        qs = urllib.parse.urlencode({"filterByFormula": formula, "maxRecords": "1",
                                     "fields[]": merge_on[0]})
        data = await self._send("GET", url=f"{self._url}?{qs}")
        recs = (data or {}).get("records") or []
        return recs[0]["id"] if recs else None

    async def create(self, fields: dict) -> dict:
        """Create one row unconditionally (no merge key, e.g. an emailless row)."""
        body = {"records": [{"fields": fields}], "typecast": True}
        return await self._send("POST", body)

    async def records(self) -> list[dict]:
        """Every record in the table (paged, read-only). Used by the mirror
        backfill. Returns raw record dicts ({id, fields, createdTime})."""
        out: list[dict] = []
        offset = None
        while True:
            url = f"{self._url}?pageSize=100" + (f"&offset={offset}" if offset else "")
            d = await self._send("GET", None, url=url)
            out.extend(d.get("records") or [])
            offset = d.get("offset")
            if not offset:
                return out

    @staticmethod
    def record_id(resp: dict) -> str | None:
        """Pull the (first) record id out of an upsert/create response."""
        recs = (resp or {}).get("records") or []
        return recs[0].get("id") if recs else None

    async def _send(self, method: str, body: dict | None = None, *,
                    url: str | None = None) -> dict:
        target = url or self._url
        kwargs: dict = {"headers": self._headers}
        if body is not None:
            kwargs["json"] = body
        resp = None
        for attempt in range(1, _MAX_RETRIES + 1):
            if self._http is not None:
                resp = await self._http.request(method, target, **kwargs)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.request(method, target, **kwargs)
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
