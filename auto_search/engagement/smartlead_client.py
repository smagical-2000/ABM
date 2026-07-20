"""SmartLead client — email outreach stats for the Outreach dashboard.

Thin async transport over the SmartLead public API (mirrors heyreach_client.py):
SmartLead owns sending/warmup/rotation; the app only READS campaign analytics
(sent/opens/clicks/replies/bounces + lead buckets) to render performance.
Base https://server.smartlead.ai/api/v1, auth is an `api_key` query param
(SMARTLEAD_API_KEY). Backs off on 429/5xx like the other executor clients.

Surface (fields verified live against campaigns 3631507-3631548, 2026-07-13):
  reads  check_key · list_campaigns (GET /campaigns) ·
         campaign_analytics (GET /campaigns/{id}/analytics)
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx

logger = logging.getLogger(__name__)

BASE = "https://server.smartlead.ai/api/v1"
_MAX_RETRIES = 4
_BACKOFF_CAP_SECONDS = 30.0


class SmartleadClient:
    """Async client; pass `http` to inject a transport in tests."""

    def __init__(self, *, api_key: str | None = None,
                 http: httpx.AsyncClient | None = None, timeout: float = 60.0) -> None:
        self._key = api_key or os.getenv("SMARTLEAD_API_KEY")
        if not self._key:
            raise RuntimeError("SMARTLEAD_API_KEY not set in .env")
        self._http = http
        self._timeout = timeout

    # ── reads ──────────────────────────────────────────────────────────

    async def check_key(self) -> bool:
        try:
            await self._request("GET", "/campaigns")
            return True
        except httpx.HTTPStatusError:
            return False

    async def list_campaigns(self) -> list[dict]:
        """Every campaign: id, name, status — the per-campaign stats loop input."""
        data = await self._request("GET", "/campaigns")
        rows = data if isinstance(data, list) else []
        return [{"id": c.get("id"), "name": c.get("name"), "status": c.get("status")}
                for c in rows]

    async def campaign_analytics(self, campaign_id: int) -> dict:
        """Raw analytics for one campaign. Counts arrive as strings ("0") —
        the aggregator coerces; this stays a faithful transport."""
        return await self._request("GET", f"/campaigns/{int(campaign_id)}/analytics")

    # ── transport ──────────────────────────────────────────────────────

    async def _request(self, method: str, path: str) -> dict | list:
        resp = None
        for attempt in range(1, _MAX_RETRIES + 1):
            resp = await self._send(method, path)
            if (resp.status_code == 429 or resp.status_code >= 500) and attempt < _MAX_RETRIES:
                wait = min(2.0 ** attempt, _BACKOFF_CAP_SECONDS)
                logger.warning("smartlead %s %s -> %s; backoff %.1fs (try %d/%d)",
                               method, path, resp.status_code, wait, attempt, _MAX_RETRIES)
                await asyncio.sleep(wait)
                continue
            resp.raise_for_status()
            try:
                return resp.json()
            except ValueError:
                return {}
        resp.raise_for_status()
        return {}

    async def _send(self, method: str, path: str) -> httpx.Response:
        url = f"{BASE}{path}"
        params = {"api_key": self._key}
        if self._http is not None:
            return await self._http.request(method, url, params=params)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await client.request(method, url, params=params)
