"""Connector interface — every signal source implements this and nothing more."""

from __future__ import annotations

from datetime import datetime
from typing import AsyncIterator, Protocol

from auto_search.models import RawSignal


class SignalConnector(Protocol):
    """Pull signals from one source, normalized to RawSignal shape.

    Implementations must be:
      - Idempotent: re-running over the same window produces the same
        source_external_ids. Downstream uses these for dedup.
      - Incremental: respect `since` to avoid re-fetching old data.
      - Failure-safe: yield what you can, log and skip what you can't.
    """

    source_name: str
    signal_types: list[str]
    default_cron: str

    async def pull(self, since: datetime) -> AsyncIterator[RawSignal]:
        """Yield signals observed after `since`. Idempotent."""
        ...
