"""Persistence layer for the Auto Search module.

  schema.sql            — Postgres schema (3 tables, dedup via constraints)
  repository.py         — DiscoveryRepository protocol + JSON-file impl
  postgres_repository.py — Postgres impl (psycopg3, sync, pooled)

The pipeline / services / UI depend only on the DiscoveryRepository protocol.
`get_repository()` is the single place that decides which backend to use, so
moving JSON → Postgres is one env var, not a code change.
"""

from __future__ import annotations

import os

from auto_search.db.repository import DiscoveryRepository, JsonFileRepository

__all__ = ["DiscoveryRepository", "JsonFileRepository", "get_repository"]


def get_repository() -> DiscoveryRepository:
    """Return the configured repository.

    Postgres when DATABASE_URL is set (production / local Postgres); otherwise
    the JSON-file repo (zero-infra default for quick local runs and tests).
    """
    if os.getenv("DATABASE_URL"):
        # Imported lazily so the JSON path never needs psycopg installed.
        from auto_search.db.postgres_repository import PostgresRepository
        return PostgresRepository()
    return JsonFileRepository()
