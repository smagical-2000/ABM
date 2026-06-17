"""Contact enrichment for export — decision-makers + verified work email & mobile.

Two steps, both user-triggered (never auto — this spends credits):
  1. Apollo finds the decision-makers at the account's domain (reuses
     scoring/apollo.py, whose ICP titles + credit cap are already tuned). Apollo
     reveal flags stay OFF, so it returns name + title + LinkedIn only.
  2. FullEnrich resolves each one's verified work email + mobile phone — an async
     bulk job (submit -> poll the enrichment_id until FINISHED).

A dedicated phone-number API can slot in front of FullEnrich later (the user's
phone → Apollo → FullEnrich waterfall); for now Apollo + FullEnrich cover it.

Degrades gracefully: no FullEnrich key, or a timeout/error, returns the Apollo
decision-makers with email/phone = None rather than failing the request.
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx

from auto_search.scoring import apollo

logger = logging.getLogger(__name__)

_FE_BASE = "https://app.fullenrich.com/api/v2"
# FullEnrich waterfalls multiple providers per contact — a 6-person bulk commonly
# takes 60-120s. We poll up to ~2.5min (activation is a deliberate, awaited action).
_POLL_TIMEOUT = 150.0
_POLL_INTERVAL = 5.0
_DONE = {"FINISHED", "CANCELED", "CREDITS_INSUFFICIENT", "UNKNOWN"}


def _key() -> str | None:
    return os.getenv("FULLENRICH_API_KEY")


async def enrich_account(domain: str | None, *, company: str | None = None,
                         http: httpx.AsyncClient | None = None) -> list[dict]:
    """Decision-makers for `domain` with verified work email + mobile, as
    [{name, title, linkedin, email, phone}]. COSTS CREDITS — call only on a user
    action. Returns [] if there's no domain / no decision-makers."""
    dms = await apollo.decision_makers(domain)
    if not dms:
        return []
    key = _key()
    if not key:
        return [{**d, "email": None, "phone": None} for d in dms]
    try:
        return await _fullenrich(dms, domain, company, key, http)
    except Exception:  # noqa: BLE001 — enrichment must never break the request
        logger.exception("FullEnrich enrichment failed for %s", domain)
        return [{**d, "email": None, "phone": None} for d in dms]


async def _fullenrich(dms: list[dict], domain: str | None, company: str | None,
                      key: str, http: httpx.AsyncClient | None) -> list[dict]:
    payload = {
        "name": f"ABM enrich {domain or ''}".strip(),
        "data": [{
            "first_name": _first(d.get("name")), "last_name": _last(d.get("name")),
            "domain": domain or "", "company_name": company or "",
            "linkedin_url": d.get("linkedin") or "",
            "enrich_fields": ["contact.work_emails", "contact.phones"],
        } for d in dms],
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    own = http is None
    client = http or httpx.AsyncClient(timeout=30.0)
    try:
        r = await client.post(f"{_FE_BASE}/contact/enrich/bulk", json=payload, headers=headers)
        r.raise_for_status()
        eid = (r.json() or {}).get("enrichment_id")
        if not eid:
            return [{**d, "email": None, "phone": None} for d in dms]
        data = await _poll(client, eid, headers)
        merged = _merge(dms, data)
        logger.info("fullenrich %s: %d/%d contacts resolved", eid,
                    sum(1 for p in merged if p.get("email") or p.get("phone")), len(merged))
        return merged
    finally:
        if own:
            await client.aclose()


async def _poll(client: httpx.AsyncClient, eid: str, headers: dict) -> list[dict]:
    waited = 0.0
    url = f"{_FE_BASE}/contact/enrich/bulk/{eid}"
    while waited < _POLL_TIMEOUT:
        r = await client.get(url, headers=headers)
        if r.status_code == 200:
            body = r.json() or {}
            if (body.get("status") or "").upper() in _DONE:
                return body.get("data") or []
        await asyncio.sleep(_POLL_INTERVAL)
        waited += _POLL_INTERVAL
    # timed out still IN_PROGRESS — return the latest snapshot we can fetch
    r = await client.get(url, headers=headers)
    return (r.json() or {}).get("data") or [] if r.status_code == 200 else []


def _merge(dms: list[dict], data: list[dict]) -> list[dict]:
    """Map FullEnrich results back to the Apollo decision-makers (submission order)."""
    out = []
    for i, d in enumerate(dms):
        ci = (data[i].get("contact_info") if i < len(data) else None) or {}
        email = (ci.get("most_probable_work_email") or {}).get("email")
        if not email and ci.get("work_emails"):
            email = ci["work_emails"][0].get("email")
        phone = (ci.get("most_probable_phone") or {}).get("number")
        if not phone and ci.get("phones"):
            phone = ci["phones"][0].get("number")
        out.append({**d, "email": email, "phone": phone})
    return out


def _first(name: str | None) -> str:
    return (name or "").strip().split(" ", 1)[0] if name else ""


def _last(name: str | None) -> str:
    parts = (name or "").strip().split(" ", 1)
    return parts[1] if len(parts) > 1 else ""
