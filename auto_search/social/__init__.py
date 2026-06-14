"""Social-listening ingestion (Apify).

People who engage with Magical's / competitors' LinkedIn posts, or attend
tracked events, arrive here as `Engager` records (scraped + enriched via Apify
in poll.py). We keep only decision-makers
(and, for events, confirmed attendees), drop Magical's own staff, then run the
COMPANY through the existing discovery qualifier — the person is carried along as
a contact in the signal payload. The company is the unit that gets scored and
ABM-matched, exactly like any other discovery signal.

    Engager(...).to_signal()                 → a RawSignal (models.py)
    ingest_engager(engager, repo=...)        → IngestResult (ingest.py)
    is_decision_maker(title, levels)         → seniority gate (seniority.py)
    is_magical(...) / is_attending(...)       → pre-qualifier gates (filters.py)
"""

from __future__ import annotations

from auto_search.social.filters import is_attending, is_magical, is_us
from auto_search.social.ingest import ingest_engager
from auto_search.social.models import (
    Engager,
    IngestResult,
    SocialSource,
    SocialTarget,
    source_for_kind,
)
from auto_search.social.poll import poll_events, poll_targets
from auto_search.social.seniority import is_decision_maker

__all__ = [
    "Engager",
    "IngestResult",
    "SocialSource",
    "SocialTarget",
    "ingest_engager",
    "is_attending",
    "is_decision_maker",
    "is_magical",
    "is_us",
    "poll_events",
    "poll_targets",
    "source_for_kind",
]
