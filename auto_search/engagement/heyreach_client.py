"""HeyReach client — LinkedIn outreach executor for campaign automation (Phase 3).

Thin async transport over the HeyReach public API (mirrors replyio_client.py):
the app decides WHO enrolls; HeyReach owns the LinkedIn flow, pacing, and the
platform caps (~200 connects/week/seat). Base https://api.heyreach.io/api/public,
auth X-API-KEY (HEYREACH_API_KEY). Backs off on 429/5xx like the Reply.io client.

Surface (all verified against the live API + the official Postman collection):
  reads   check_key · list_campaigns (POST /campaign/GetAll) · list_senders
          (POST /li_account/GetAll)
  writes  add_leads_to_campaign (POST /campaign/AddLeadsToCampaignV2) ·
          stop_lead (POST /campaign/StopLeadInCampaign — the stop-rule lever) ·
          create_webhook (POST /webhooks/CreateWebhook)

HeyReach leads are keyed by LinkedIn PROFILE URL and each lead is pinned to a
sender account id — so enrollment requires (a) >=1 connected sender and (b) a
lead linkedin_url. Both absences are expected states the runner reports, not
errors.
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx

logger = logging.getLogger(__name__)

BASE = "https://api.heyreach.io/api/public"
_MAX_RETRIES = 4
_BACKOFF_CAP_SECONDS = 30.0


class HeyReachClient:
    """Async client; pass `http` to inject a transport in tests."""

    def __init__(self, *, api_key: str | None = None,
                 http: httpx.AsyncClient | None = None, timeout: float = 60.0) -> None:
        self._key = api_key or os.getenv("HEYREACH_API_KEY")
        if not self._key:
            raise RuntimeError("HEYREACH_API_KEY not set in .env")
        self._http = http
        self._timeout = timeout

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-API-KEY": self._key, "Content-Type": "application/json"}

    # ── reads ──────────────────────────────────────────────────────────

    async def check_key(self) -> bool:
        r = await self._send("GET", "/auth/CheckApiKey")
        return r.status_code == 200

    async def list_campaigns(self, *, limit: int = 100) -> list[dict]:
        """Every campaign: id, name, status, sender count — for the mapping picker."""
        data = await self._request("POST", "/campaign/GetAll",
                                   json={"offset": 0, "limit": limit})
        return [{"id": c.get("id"), "name": c.get("name"), "status": c.get("status"),
                 "senders": len(c.get("campaignAccountIds") or [])}
                for c in (data.get("items") or [])]

    async def list_senders(self, *, limit: int = 50) -> list[dict]:
        """Connected LinkedIn accounts (the seats). Empty until MAR2-6 lands."""
        data = await self._request("POST", "/li_account/GetAll",
                                   json={"offset": 0, "limit": limit})
        return [{"id": a.get("id"),
                 "name": f"{a.get('firstName') or ''} {a.get('lastName') or ''}".strip(),
                 "active": a.get("isActive")}
                for a in (data.get("items") or [])]

    # ── writes ─────────────────────────────────────────────────────────

    async def add_leads_to_campaign(self, *, campaign_id: int,
                                    leads: list[dict],
                                    sender_ids: list[int]) -> dict:
        """Push leads into a campaign, round-robining them across `sender_ids`.
        Lead dicts need `profileUrl` (the HeyReach identity key); firstName /
        lastName / companyName / position are carried for personalization.
        Returns HeyReach's counts: {addedLeadsCount, updatedLeadsCount, failedLeadsCount}."""
        if not sender_ids:
            raise ValueError("no LinkedIn sender accounts connected")
        pairs = [{"linkedInAccountId": sender_ids[i % len(sender_ids)],
                  "lead": lead} for i, lead in enumerate(leads)]
        return await self._request("POST", "/campaign/AddLeadsToCampaignV2",
                                   json={"campaignId": int(campaign_id),
                                         "accountLeadPairs": pairs,
                                         "resumeFinishedCampaign": False,
                                         "resumePausedCampaign": False})

    async def stop_lead(self, *, campaign_id: int, profile_url: str) -> bool:
        """Stop one lead inside a campaign — the pause-other-channels lever.
        True on success; False (logged) on failure so a stop sweep never raises."""
        try:
            await self._request("POST", "/campaign/StopLeadInCampaign",
                                json={"campaignId": int(campaign_id),
                                      "leadMemberId": None,
                                      "leadUrl": profile_url})
            return True
        except httpx.HTTPStatusError as e:
            logger.warning("heyreach stop_lead failed for %s in %s: %s",
                           profile_url, campaign_id, e)
            return False

    async def create_webhook(self, *, name: str, url: str, event_type: str,
                             campaign_ids: list[int] | None = None) -> dict:
        """Register a webhook (e.g. MESSAGE_REPLY_RECEIVED -> our /api receiver)."""
        return await self._request("POST", "/webhooks/CreateWebhook",
                                   json={"webhookName": name, "webhookUrl": url,
                                         "eventType": event_type,
                                         "campaignIds": campaign_ids or []})

    # ── transport ──────────────────────────────────────────────────────

    async def _request(self, method: str, path: str, *, json: dict | None = None) -> dict:
        resp = None
        for attempt in range(1, _MAX_RETRIES + 1):
            resp = await self._send(method, path, json=json)
            if (resp.status_code == 429 or resp.status_code >= 500) and attempt < _MAX_RETRIES:
                wait = min(2.0 ** attempt, _BACKOFF_CAP_SECONDS)
                logger.warning("heyreach %s %s -> %s; backoff %.1fs (try %d/%d)",
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

    async def _send(self, method: str, path: str, *, json: dict | None = None) -> httpx.Response:
        url = f"{BASE}{path}"
        if self._http is not None:
            return await self._http.request(method, url, headers=self._headers, json=json)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await client.request(method, url, headers=self._headers, json=json)
