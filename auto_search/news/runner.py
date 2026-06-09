"""Pull headlines, enrich the new ones, store — the daily news job + manual refresh.

Only NEW urls are enriched and stored, so we never re-pay to classify an article
we've already seen. Storage is best-effort via duck-typed repo methods
(`news_urls` / `save_news_items`), so a repo without them is a harmless no-op.
"""

from __future__ import annotations

import logging

from auto_search.news import enrich as _enrich
from auto_search.news import feeds

logger = logging.getLogger(__name__)


async def run_once(repo, *, max_per_query: int = 15, do_enrich: bool = True,
                   on_cost=None) -> dict:
    """Fetch -> filter to new -> enrich -> store. Returns a run summary."""
    items = await feeds.fetch_all(max_per_query=max_per_query)

    existing = set(repo.news_urls()) if hasattr(repo, "news_urls") else set()
    fresh = [it for it in items if it.url not in existing]

    cost = 0.0
    if do_enrich and fresh:
        cost = await _enrich.enrich(fresh)
        if on_cost and cost:
            try:
                on_cost(cost)
            except Exception:  # noqa: BLE001 — accounting must not break the run
                logger.exception("news on_cost hook failed")

    keep = [it for it in fresh if it.relevant]
    stored = 0
    if hasattr(repo, "save_news_items") and keep:
        stored = repo.save_news_items([it.model_dump() for it in keep])

    summary = {
        "fetched": len(items), "new": len(fresh), "stored": stored,
        "dropped_irrelevant": len(fresh) - len(keep), "cost_usd": round(cost, 4),
    }
    logger.info("news run: %s", summary)
    return summary
