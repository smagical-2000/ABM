"""Warm-intros orchestration: founders -> contacts -> paths -> payload.

The payload persists on the scored account (warm_intros JSONB) with its state
inside, mirroring the dossier lifecycle: generating -> ready | error. Founders
are scraped once and cached in the discovery repo; a re-run reuses them.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from auto_search.intros import paths, profiles
from auto_search.intros.models import FounderProfile
from auto_search.social.seniority import is_decision_maker

logger = logging.getLogger(__name__)

_WS = re.compile(r"[^a-z0-9]+")


async def ensure_founders(repo, *, on_cost=None) -> list[FounderProfile]:
    """Founder profiles from the repo cache, scraping any missing one once.

    `repo` is the discovery repository (founder_profiles / replace_founder_profiles).
    A founder whose scrape fails is skipped (matching degrades gracefully).
    """
    cached = {p.get("linkedin_url"): p for p in (repo.founder_profiles() or [])}
    out: list[FounderProfile] = []
    fresh = False
    for url in profiles.founder_urls():
        row = cached.get(url)
        if row:
            try:
                out.append(FounderProfile(**row))
                continue
            except Exception:  # noqa: BLE001 — stale shape -> rescrape
                pass
        try:
            fp = await profiles.fetch_founder(url)
        except Exception:  # noqa: BLE001 — one founder failing mustn't kill the run
            logger.exception("founder scrape failed for %s", url)
            fp = None
        if fp is not None:
            out.append(fp)
            fresh = True
            if on_cost is not None:
                on_cost(profiles.FOUNDER_COST_USD, "founder_profile")
    if fresh and out:
        repo.replace_founder_profiles([p.model_dump() for p in out])
    return out


def engaged_identity_sets(discovery_repo, company_key: str | None) -> tuple[set, set]:
    """Profile-URL slugs + normalized names of people who engaged with Magical,
    from the company's stored social signals. Empty sets when unknown."""
    urls: set[str] = set()
    names: set[str] = set()
    if not company_key:
        return urls, names
    try:
        row = discovery_repo.get(company_key)
    except Exception:  # noqa: BLE001 — engagement enrichment is best-effort
        row = None
    for s in ((row or {}).get("signals") or []):
        if s.get("signal_type") not in ("social_engagement", "event_attendance"):
            continue
        p = s.get("payload") or {}
        slug = paths._profile_slug(p.get("person_profile_url"))
        if slug:
            urls.add(slug)
        nm = _WS.sub(" ", (p.get("person_name") or "").lower()).strip()
        if nm:
            names.add(nm)
    return urls, names


async def generate(account: dict, *, discovery_repo, on_cost=None) -> dict:
    """Build the warm-intros payload for one scored account. Raises on a dead
    search so the caller can persist state='error' (retryable)."""
    company = account.get("name") or ""
    domain = account.get("domain")
    founders = await ensure_founders(discovery_repo, on_cost=on_cost)

    # Apollo first - free, matches on the account's DOMAIN (so it never returns a
    # plumbing CFO for a cancer institute the way a name search can) and filters
    # to real decision-makers. Fall back to the Apify people search only when
    # Apollo finds nothing (no key, no domain, or a thin org).
    source = "apollo"
    parsed_contacts = [p for p in
                       (profiles.parse_apollo(it) for it in await profiles.apollo_contacts(domain))
                       if p]
    if not parsed_contacts:
        source = "apify"
        items = await profiles.search_contacts(company)
        if on_cost is not None and items:
            on_cost(round(len(items) * profiles.CONTACT_COST_USD, 4), "contact_search")
        parsed_contacts = [p for p in (profiles.parse_contact(it) for it in items) if p]

    engaged_urls, engaged_names = engaged_identity_sets(
        discovery_repo, account.get("discovery_company_key"))

    contacts = []
    dropped = 0
    for parsed in parsed_contacts:
        contact, exp, edu = parsed
        # The board search matches titles loosely; hold the product bar here
        # (Director & above) so a "Revenue Cycle Supervisor" never ships.
        if not is_decision_maker(contact.title)[0]:
            dropped += 1
            continue
        for f in founders:
            contact.paths.extend(paths.founder_paths(f, contact, exp, edu))
        ep = paths.engaged_path(contact, engaged_urls, engaged_names)
        if ep:
            contact.paths.insert(0, ep)
        contact.paths.sort(key=lambda p: -p.strength)
        contacts.append(contact)

    ranked = paths.rank(contacts)
    warm = sum(1 for c in ranked if c.warmth > 0)
    logger.info("warm intros for %s: %d contacts via %s (%d warm, %d sub-bar dropped)",
                company, len(ranked), source, warm, dropped)
    return {
        "state": "ready",
        "generated_at": datetime.now(UTC).isoformat(),
        "source": source,
        "founders_used": [f.name for f in founders],
        "contacts": [
            {**c.model_dump(), "warmth": c.warmth} for c in ranked
        ],
        "warm_count": warm,
    }
