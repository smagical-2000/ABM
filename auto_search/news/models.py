"""DTOs for the market-intelligence news feed."""

from __future__ import annotations

from pydantic import BaseModel

# The topic chips the News tab filters on. The enrich pass classifies each
# article into exactly one of these (or marks it not relevant and drops it).
TOPICS = ("prior_auth", "denials", "rcm_ai", "eligibility", "policy", "operations")

# Human labels for the UI / prompt.
TOPIC_LABELS = {
    "prior_auth": "Prior Auth",
    "denials": "Denials",
    "rcm_ai": "RCM / AI",
    "eligibility": "Eligibility",
    "policy": "CMS / Policy",
    "operations": "Operations",
}


class NewsItem(BaseModel):
    """One news headline, with the AI-assigned topic + 'why it matters' angle."""

    url: str                              # canonical link — the dedup key
    title: str
    source: str | None = None             # publication name
    published_at: str | None = None       # ISO-8601
    snippet: str | None = None            # best-effort, from the feed
    topic: str | None = None              # one of TOPICS (assigned by enrich)
    why_it_matters: str | None = None     # one-line angle for an RCM-automation seller
    relevant: bool = True                 # enrich drops the rest
    fetched_at: str | None = None
