"""Apify profile fetching + normalization for warm intros.

Two actors (both validated against live output in the dry run):
  - freshdata~fresh-linkedin-profile-data: one founder URL -> full profile with
    experiences[] (company, start/end year, is_current) and educations[].
  - harvestapi~linkedin-profile-search: decision-makers at an account -
    currentCompanies (plain company name resolves) + currentJobTitles filters,
    profileScraperMode=Full so each hit carries experience[] + education[].

Costs are conservative flat rates recorded per call (same stance as social):
the search bills per profile; founders are a one-time ~3-profile scrape.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime

from auto_search.intros.models import FounderProfile, Stint, WarmContact
from auto_search.intros.paths import norm_company, norm_school
from auto_search.scoring import apollo
from auto_search.social import apify

logger = logging.getLogger(__name__)

_ACTOR_ENRICH = "freshdata~fresh-linkedin-profile-data"
_ACTOR_SEARCH = "harvestapi~linkedin-profile-search"

# Conservative recorded costs (freshdata ~ $8/1k; harvest Full profile ~ $10/1k).
FOUNDER_COST_USD = 0.01
CONTACT_COST_USD = 0.015

# The people whose networks we match against ("founders" is the legacy field name;
# the roster is really the whole go-to-market team). The sales team (AEs/SDRs) is the
# high-value half — their networks reach hospital execs the founders' don't. Stored
# https + trailing-slash (the shape freshdata accepts). Env-overridable without a deploy.
DEFAULT_FOUNDER_URLS = (
    # founders
    "https://www.linkedin.com/in/hsambhi/",
    "https://www.linkedin.com/in/rosiechopra/",
    "https://www.linkedin.com/in/geoffreygmartin/",
    # sales team (AEs / SDRs)
    "https://www.linkedin.com/in/justingernot/",
    "https://www.linkedin.com/in/matt-royalty-a5416a27/",
    "https://www.linkedin.com/in/colin-m-43248367/",
    "https://www.linkedin.com/in/ben-davies-4b794620b/",
    "https://www.linkedin.com/in/gabriel-hanna-9030981b0/",
    "https://www.linkedin.com/in/justin-pride-255b466/",
    "https://www.linkedin.com/in/alykhan-jina-73b247102/",
)


def founder_urls() -> list[str]:
    raw = os.getenv("WARM_INTRO_FOUNDER_URLS", "")
    urls = [u.strip() for u in raw.split(",") if u.strip()]
    return urls or list(DEFAULT_FOUNDER_URLS)


# The ICP titles to search at each account - condensed from the user's target
# list (Health Systems + Rural Health; C-suite / VP / Director). The search
# treats each as a current-job-title filter; the seniority post-filter in
# service.py drops sub-Director noise the board search lets through.
ICP_TITLES = [
    "Chief Executive Officer", "Chief Operating Officer", "Chief Financial Officer",
    "Chief Information Officer", "Chief Revenue Officer", "Chief Innovation Officer",
    "Chief Digital Officer", "Chief Transformation Officer", "Chief Medical Information Officer",
    "VP Revenue Cycle", "Vice President Revenue Cycle", "VP of Patient Access",
    "VP of Patient Financial Services", "VP of Finance", "VP of Innovation",
    "VP of Transformation", "VP of Digital Transformation", "VP of Utilization Management",
    "Director of Revenue Cycle", "Director of Patient Access", "Director of Finance",
    "Director of Patient Financial Services", "Director of Utilization Management",
    "Director of Health Information Technology",
]


def max_contacts() -> int:
    return max(1, int(os.getenv("INTROS_MAX_CONTACTS", "8")))


def _year(v) -> int | None:
    try:
        n = int(v)
        return n or None
    except (TypeError, ValueError):
        return None


# ── founders (freshdata) ──────────────────────────────────────────────


def _founder_stints(raw: dict) -> tuple[list[Stint], list[Stint]]:
    exp = []
    for e in raw.get("experiences") or []:
        company = (e.get("company") or "").strip()
        if not company:
            continue
        end = _year(e.get("end_year"))
        exp.append(Stint(
            org=company, norm=norm_company(company), title=e.get("title"),
            start_year=_year(e.get("start_year")),
            end_year=9999 if (e.get("is_current") or not end) else end))
    edu = []
    for e in raw.get("educations") or []:
        school = (e.get("school") or "").strip()
        if not school:
            continue
        edu.append(Stint(
            org=school, norm=norm_school(school), title=e.get("degree"),
            start_year=_year(e.get("start_year")),
            end_year=_year(e.get("end_year")) or 9999))
    return exp, edu


async def fetch_founder(url: str) -> FounderProfile | None:
    """Scrape one connector profile -> FounderProfile, or None if unresolvable.
    Normalizes the URL (freshdata 400s on Apollo's http:// / no-trailing-slash shape)
    so any roster entry works regardless of how it was pasted."""
    items = await apify._run_actor(_ACTOR_ENRICH,
                                   {"linkedin_url": normalize_linkedin_url(url) or url})
    if not items or not isinstance(items[0], dict):
        return None
    raw = items[0].get("data") if isinstance(items[0].get("data"), dict) else items[0]
    name = raw.get("full_name") or " ".join(
        x for x in (raw.get("first_name"), raw.get("last_name")) if x)
    if not name:
        return None
    exp, edu = _founder_stints(raw)
    return FounderProfile(
        name=name, linkedin_url=url, headline=raw.get("headline"),
        experiences=exp, educations=edu,
        scraped_at=datetime.now(UTC).isoformat())


# ── contacts (harvest people search, Full mode) ───────────────────────


def _contact_stints(item: dict) -> tuple[list[Stint], list[Stint]]:
    exp = []
    for e in item.get("experience") or []:
        company = (e.get("companyName") or "").strip()
        if not company:
            continue
        end_raw = e.get("endDate") or {}
        end = _year(end_raw.get("year"))
        present = "present" in str(end_raw.get("text") or "").lower()
        exp.append(Stint(
            org=company, norm=norm_company(company), title=e.get("position"),
            start_year=_year((e.get("startDate") or {}).get("year")),
            end_year=9999 if (present or not end) else end))
    edu = []
    for e in item.get("education") or []:
        school = (e.get("schoolName") or "").strip()
        if not school:
            continue
        edu.append(Stint(
            org=school, norm=norm_school(school), title=e.get("degree"),
            start_year=_year((e.get("startDate") or {}).get("year")),
            end_year=_year((e.get("endDate") or {}).get("year")) or 9999))
    return exp, edu


def parse_contact(item: dict) -> tuple[WarmContact, list[Stint], list[Stint]] | None:
    """One Full-mode search hit -> (contact, experiences, educations)."""
    if not isinstance(item, dict):
        return None
    name = " ".join(x for x in (item.get("firstName"), item.get("lastName")) if x).strip()
    if not name:
        return None
    loc = item.get("location") or {}
    title = item.get("headline")
    if not title:
        cur = item.get("currentPosition") or item.get("currentPositions") or []
        if cur and isinstance(cur, list) and isinstance(cur[0], dict):
            title = cur[0].get("position") or cur[0].get("companyName")
    exp, edu = _contact_stints(item)
    contact = WarmContact(
        name=name, title=title,
        linkedin_url=item.get("linkedinUrl"),
        location=loc.get("linkedinText") if isinstance(loc, dict) else None)
    return contact, exp, edu


_PAREN = re.compile(r"\s*\([^)]*\)")                       # "(AKA Infinity … PC)"
_AKA = re.compile(r"\s*\b(a\.?k\.?a\.?|d\.?b\.?a\.?|f\.?k\.?a\.?)\b.*$", re.I)  # "… AKA …"
_LEGAL = re.compile(r"[\s,]+(p\.?c\.?|pllc|llc|llp|inc|corp|co|ltd)\.?\s*$", re.I)


def search_company_name(name: str | None) -> str:
    """Clean a stored ABM name into something LinkedIn's company search can match.
    Drops parentheticals ('(AKA …)'), trailing AKA/DBA aliases, and a legal suffix
    (PC/LLC/Inc/…). e.g. 'Radiology Alliance (AKA Infinity Management & Radiology
    Alliance PC)' -> 'Radiology Alliance'. Never returns empty (falls back to raw)."""
    s = _PAREN.sub("", name or "")
    s = _AKA.sub("", s)
    s = re.sub(r"\s+", " ", s).strip().strip(",").strip()
    s = _LEGAL.sub("", s).strip().strip(",").strip()
    return s or (name or "").strip()


async def search_contacts(company_name: str, *, limit: int | None = None) -> list[dict]:
    """Raw Full-mode search hits for ICP decision-makers at `company_name`. The name
    is cleaned first so messy ABM entries ('… (AKA …)', '… PC') match a real company."""
    return await apify._run_actor(_ACTOR_SEARCH, {
        "currentCompanies": [search_company_name(company_name)],
        "currentJobTitles": ICP_TITLES,
        "profileScraperMode": "Full",
        "maxItems": limit or max_contacts(),
    })


# ── Apollo contacts (primary - free, domain-matched, seniority-filtered) ──


_YEAR = re.compile(r"(\d{4})")


def _year_of(s) -> int | None:
    m = _YEAR.match(str(s or ""))
    return int(m.group(1)) if m else None


async def apollo_contacts(domain: str | None) -> list[dict]:
    """Senior decision-makers at `domain` via Apollo (free). [] -> use Apify."""
    return await apollo.contacts_for_intros(domain)


def parse_apollo(item: dict) -> tuple[WarmContact, list[Stint], list[Stint]] | None:
    """One Apollo contact -> (contact, experiences, educations[]). Apollo gives
    employment history (matchable) but no schools, so educations is always []."""
    if not isinstance(item, dict):
        return None
    name = (item.get("name") or "").strip()
    if not name:
        return None
    exp: list[Stint] = []
    for e in item.get("employment_history") or []:
        org = (e.get("org") or "").strip()
        if not org:
            continue
        end = _year_of(e.get("end"))
        exp.append(Stint(
            org=org, norm=norm_company(org), title=e.get("title"),
            start_year=_year_of(e.get("start")),
            end_year=9999 if (e.get("current") or not end) else end))
    loc = ", ".join(x for x in (item.get("city"), item.get("state")) if x) or None
    contact = WarmContact(
        name=name, title=item.get("title"),
        linkedin_url=(item.get("linkedin") or None), location=loc)
    return contact, exp, []


# ── ICP contact profile scrape (freshdata) ───────────────────────────
# Apollo finds the right decision-makers (free, domain-precise) but its data is
# employment-only. To cross-match the same way both sides do, we scrape each kept
# decision-maker's FULL profile via the same Apify actor as the team — so employer
# AND school paths can fire (a shared alma mater is the widest warm net: a
# university has orders of magnitude more alumni than an ex-employer). freshdata
# 400s on Apollo's raw URL shape (http://, no trailing slash), so normalize first.

# freshdata ~ $9/1k per profile (the user's quoted rate): one scrape per kept DM.
ENRICH_CONTACT_COST_USD = 0.009


def normalize_linkedin_url(url: str | None) -> str | None:
    """Apollo's `http://www.linkedin.com/in/slug` -> the `https://.../in/slug/`
    form freshdata accepts. None when it isn't a personal LinkedIn profile URL."""
    if not url:
        return None
    u = url.strip().replace("http://", "https://")
    if "linkedin.com/in/" not in u:
        return None
    return u.rstrip("/") + "/"


async def fetch_contact_stints(normalized_url: str) -> tuple[list[Stint], list[Stint]]:
    """freshdata scrape of one ICP profile (URL already normalized) -> (experience,
    education) Stints — the full profile, so the contact cross-matches the team on
    both employer and school. ([], []) on any failure, so a single dead scrape never
    kills the batch — the contact just stays on its Apollo data and matching degrades."""
    try:
        items = await apify._run_actor(_ACTOR_ENRICH, {"linkedin_url": normalized_url})
    except Exception:  # noqa: BLE001 — one scrape failing mustn't break the run
        logger.exception("contact scrape failed for %s", normalized_url)
        return [], []
    if not items or not isinstance(items[0], dict):
        return [], []
    raw = items[0].get("data") if isinstance(items[0].get("data"), dict) else items[0]
    return _founder_stints(raw)          # (experience, education) — same parser as the team
