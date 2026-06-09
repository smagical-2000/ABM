"""Market-intelligence news — RCM / regulation headlines for the GTM team.

Distinct from the company-signal pipeline: this is *industry context* (CMS rules,
prior-auth changes, denial trends, healthcare-AI), not a per-company buying
signal. A daily RSS pull (free) gathers headlines; one cheap batched Sonnet pass
tags each with a topic + a one-line "why it matters for an RCM-automation seller".

    fetch_all()           -> list[NewsItem]     (feeds.py, free)
    enrich(items)         -> LlmSpend           (enrich.py, ~pennies/day)
    run_once(repo)        -> summary            (runner.py)
"""

from __future__ import annotations

from auto_search.news.models import TOPIC_LABELS, TOPICS, NewsItem
from auto_search.news.runner import run_once

__all__ = ["TOPICS", "TOPIC_LABELS", "NewsItem", "run_once"]
