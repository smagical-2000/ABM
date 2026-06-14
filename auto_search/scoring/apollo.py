"""Apollo enrichment — decision-maker names + titles for the landing-page dossier.

Names come from Apollo, not the LLM: deterministic, no people web-search (saving
tokens), and no name hallucination. We take name + title + LinkedIn only and
never request or keep emails or phone numbers (the reveal flags stay off).

Search returns titles with the last name obfuscated; enrichment (one Apollo
credit per person, capped) returns the full name. Any failure degrades to an
empty list so the dossier still generates.
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://api.apollo.io/api/v1"
_TIMEOUT = 20.0
# How many people we enrich per dossier — bounds the Apollo credit spend.
_MAX_PEOPLE = 6
# The RCM-buyer + executive personas Magical sells into.
_TITLES = [
    "Chief Executive Officer", "Chief Financial Officer", "Chief Operating Officer",
    "Chief Information Officer", "Chief Medical Officer",
    "Vice President Revenue Cycle", "VP Revenue Cycle",
    "Director Revenue Cycle", "Revenue Cycle", "VP Finance",
    "General Counsel", "Practice Administrator",
]


def _key() -> str | None:
    return os.getenv("APOLLO_API_KEY")


async def decision_makers(domain: str | None) -> list[dict]:
    """Return [{name, title, linkedin}] for an account's domain, or [] if Apollo
    is unconfigured, the domain is missing, or anything goes wrong."""
    key = _key()
    if not key or not domain:
        return []
    headers = {"X-Api-Key": key, "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=headers) as client:
            found = await _search(client, domain)
            if not found:
                return []
            # Enrich the top matches in parallel for full names.
            enriched = await asyncio.gather(
                *(_enrich(client, p.get("id")) for p in found[:_MAX_PEOPLE]),
                return_exceptions=True,
            )
            return _shape(found, enriched)
    except Exception as e:  # noqa: BLE001 — enrichment must never break the dossier
        logger.warning("Apollo enrichment failed for %s: %s", domain, e)
        return []


async def _search(client: httpx.AsyncClient, domain: str) -> list[dict]:
    r = await client.post(f"{_BASE}/mixed_people/api_search", json={
        "q_organization_domains_list": [domain],
        "person_titles": _TITLES,
        "per_page": _MAX_PEOPLE + 4,
        "page": 1,
    })
    if r.status_code != 200:
        logger.warning("Apollo search %s -> HTTP %s", domain, r.status_code)
        return []
    return (r.json() or {}).get("people") or []


async def _enrich(client: httpx.AsyncClient, person_id: str | None) -> dict | None:
    if not person_id:
        return None
    # reveal flags OFF: we want the name, never the email or phone number.
    r = await client.post(f"{_BASE}/people/match", json={
        "id": person_id,
        "reveal_personal_emails": False,
        "reveal_phone_number": False,
    })
    if r.status_code != 200:
        return None
    return (r.json() or {}).get("person")


def _shape(found: list[dict], enriched: list) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    # enriched aligns with found[:_MAX_PEOPLE]; zip stops at the shorter.
    for base, full in zip(found, enriched, strict=False):
        person = full if isinstance(full, dict) else {}
        name = (person.get("name") or base.get("first_name") or "").strip()
        title = (person.get("title") or base.get("title") or "").strip()
        if not name or not title:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "name": name,
            "title": title,
            "linkedin": (person.get("linkedin_url") or "").strip(),
        })
    return out


# ── decision-maker contacts for warm intros ───────────────────────────
# Seniority-filtered so we get actual decision-makers (CFO / CIO / VP RevCycle /
# Director), not the "Revenue Cycle Specialist" the title keyword alone matches.
# We also carry employment_history (with years) for founder-overlap matching.
_INTRO_SENIORITIES = ["c_suite", "vp", "director", "owner", "founder", "partner", "head"]
_INTRO_TITLES = [
    "revenue cycle", "patient access", "patient financial", "finance",
    "information", "digital", "transformation", "innovation", "operations",
    "analytics", "utilization management", "managed care",
]


def _intro_max() -> int:
    return max(1, int(os.getenv("INTROS_MAX_CONTACTS", "8")))


async def contacts_for_intros(domain: str | None) -> list[dict]:
    """Senior decision-makers at `domain` with employment history, for warm
    intros: [{name, title, linkedin, city, state, employment_history:[...]}].

    [] when Apollo is unconfigured, the domain is missing, or anything fails -
    the caller then falls back to the Apify people search. Apollo is free, so
    this carries no recorded cost.
    """
    key = _key()
    if not key or not domain:
        return []
    headers = {"X-Api-Key": key, "Content-Type": "application/json"}
    cap = _intro_max()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=headers) as client:
            r = await client.post(f"{_BASE}/mixed_people/api_search", json={
                "q_organization_domains_list": [domain],
                "person_seniorities": _INTRO_SENIORITIES,
                "person_titles": _INTRO_TITLES,
                "per_page": cap + 4, "page": 1,
            })
            if r.status_code != 200:
                logger.warning("Apollo intro search %s -> HTTP %s", domain, r.status_code)
                return []
            found = (r.json() or {}).get("people") or []
            enriched = await asyncio.gather(
                *(_enrich(client, p.get("id")) for p in found[:cap]),
                return_exceptions=True,
            )
            return _shape_rich(found, enriched)
    except Exception as e:  # noqa: BLE001 — never break warm intros
        logger.warning("Apollo intro contacts failed for %s: %s", domain, e)
        return []


def _shape_rich(found: list[dict], enriched: list) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for base, full in zip(found, enriched, strict=False):
        person = full if isinstance(full, dict) else {}
        name = (person.get("name")
                or " ".join(x for x in (base.get("first_name"), base.get("last_name")) if x)
                or "").strip()
        title = (person.get("title") or base.get("title") or "").strip()
        if not name or not title:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "name": name,
            "title": title,
            "linkedin": (person.get("linkedin_url") or "").strip(),
            "city": person.get("city"),
            "state": person.get("state"),
            "employment_history": [
                {"org": e.get("organization_name"), "title": e.get("title"),
                 "start": e.get("start_date"), "end": e.get("end_date"),
                 "current": bool(e.get("current"))}
                for e in (person.get("employment_history") or [])
                if e.get("organization_name")
            ],
        })
    return out
