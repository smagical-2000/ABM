"""Competitor distress monitor — negative press on our competitors → News/Discovery.

The competitor set is small and known, so a handful of named Google News queries
(free RSS, the same pipeline as industry news) catches layoffs / shutdowns / lawsuits
/ funding trouble / breaches cheaply. Hits are stored as `news_items` under a
"Competitor: <name>" topic so they surface in the News panel (a Discovery-phase
surface) with the fast-follower play (go after a struggling competitor's customers).

The competitor list IS the monitored `social_targets` rows with kind='competitor'
(the same list the social poll already watches), so adding a competitor in the
platform automatically extends both social and news monitoring. `COMPETITORS` below
seeds the launch set; more can be added via POST /api/social/targets.
"""

from __future__ import annotations

import logging

from auto_search.news import feeds

logger = logging.getLogger(__name__)

# Launch competitor set (LinkedIn URL + display name). The name drives the news
# query, so keep it the form a journalist would write. A target with no usable
# name (e.g. a numeric LinkedIn id) is still monitored socially but skipped for news.
COMPETITORS: list[dict] = [
    {"linkedin_url": "https://www.linkedin.com/company/r1-rcm/", "label": "R1 RCM"},
    {"linkedin_url": "https://www.linkedin.com/company/arintra", "label": "Arintra"},
    {"linkedin_url": "https://www.linkedin.com/company/join-honey-health/", "label": "Honey Health"},
    {"linkedin_url": "https://www.linkedin.com/company/cloudcruise/", "label": "CloudCruise"},
    {"linkedin_url": "https://linkedin.com/company/celonis/", "label": "Celonis"},
    {"linkedin_url": "https://www.linkedin.com/company/youramigo-ai/", "label": "YourAmigo AI"},
    {"linkedin_url": "https://www.linkedin.com/company/entropyhealth", "label": "Entropy Health"},
    {"linkedin_url": "https://www.linkedin.com/company/silna-health", "label": "Silna Health"},
    {"linkedin_url": "https://www.linkedin.com/company/amerahealthsolutions/", "label": "Amera Health Solutions"},
    {"linkedin_url": "https://www.linkedin.com/company/assorthealth/", "label": "AssortHealth"},
    {"linkedin_url": "https://www.linkedin.com/company/adonis-technologies/", "label": "Adonis Technologies"},
    {"linkedin_url": "https://www.linkedin.com/company/nottelabsinc/", "label": "Notte Labs"},
    {"linkedin_url": "https://www.linkedin.com/company/superdial", "label": "SuperDial"},
    {"linkedin_url": "https://www.linkedin.com/company/skypoint-ai/", "label": "Skypoint AI"},
    {"linkedin_url": "https://www.linkedin.com/company/openbots/", "label": "OpenBots"},
    {"linkedin_url": "https://www.linkedin.com/company/10455871", "label": None},  # numeric id — social only
    {"linkedin_url": "https://linkedin.com/company/tennrai/", "label": "Tennr"},
    {"linkedin_url": "https://www.linkedin.com/company/uipath", "label": "UiPath"},
]

# Distress / negative-press terms — the query keeps only headlines near these.
_NEGATIVE = (
    'layoffs OR "lays off" OR "laid off" OR shutdown OR "shuts down" OR "shutting down" '
    'OR lawsuit OR sued OR "data breach" OR breach OR outage OR bankruptcy OR insolvency '
    'OR restructuring OR "wind down" OR "winding down" OR fired OR resigns OR resignation '
    'OR fine OR penalty OR "security incident" OR downtime OR "service disruption" '
    'OR "missed payroll" OR "funding trouble" OR "going out of business"'
)

TOPIC_PREFIX = "Competitor: "


def competitor_query(name: str) -> str:
    """Google News query: the competitor name (quoted) near any distress term."""
    return f'"{name.strip()}" ({_NEGATIVE})'


def competitor_names(targets: list[dict]) -> list[str]:
    """Usable competitor names from monitored social targets (kind='competitor'
    + active + a non-empty label). Deduped, order-preserving."""
    out: list[str] = []
    seen: set[str] = set()
    for t in targets or []:
        # Only EXPLICIT competitors — a legacy row with no `kind` is not assumed a
        # competitor (it would otherwise get news-monitored by accident).
        if t.get("kind") != "competitor" or not t.get("active", True):
            continue
        name = (t.get("label") or "").strip()
        key = name.lower()
        if name and key not in seen:
            seen.add(key)
            out.append(name)
    return out


def build_queries(names: list[str]) -> dict[str, str]:
    """{topic: query} for the competitor news fetch, topic = 'Competitor: <name>'."""
    return {f"{TOPIC_PREFIX}{n}": competitor_query(n) for n in names}


async def run_competitor_news(repo, *, max_per: int = 8, recency: str = "30d") -> dict:
    """Fetch competitor distress headlines -> keep the new ones -> store as
    news_items tagged with the fast-follower play. Idempotent (dedup by URL).

    Reads the competitor list from the repo's social_targets so it always tracks
    what's been added in the platform. No-op on a repo without the news methods."""
    targets = repo.social_targets() if hasattr(repo, "social_targets") else []
    names = competitor_names(targets)
    queries = build_queries(names)
    if not queries:
        return {"competitors": 0, "items": 0, "stored": 0}

    items = await feeds.fetch_queries(queries, max_per_query=max_per, recency=recency)
    existing = set(repo.news_urls()) if hasattr(repo, "news_urls") else set()
    fresh = [it for it in items if it.url not in existing]
    for it in fresh:
        name = it.topic[len(TOPIC_PREFIX):] if it.topic.startswith(TOPIC_PREFIX) else "a competitor"
        it.relevant = True
        it.why_it_matters = f"Possible distress signal at {name} (a Magical competitor)."
        it.play = (f"Fast-follower: target {name}'s customers — they may be open to "
                   f"switching; lead with reliable RCM automation.")
        it.get_behind = 60

    stored = 0
    if fresh and hasattr(repo, "save_news_items"):
        repo.save_news_items([it.model_dump() for it in fresh])
        stored = len(fresh)
    summary = {"competitors": len(queries), "items": len(items), "stored": stored}
    logger.info("competitor news: %s", summary)
    return summary
