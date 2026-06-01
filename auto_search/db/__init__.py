"""Persistence layer for the Auto Search module.

  schema.sql       — target Postgres schema (3 tables, dedup via constraints)
  repository.py     — storage interface + JSON-file impl (runs without Postgres)

The pipeline depends only on the DiscoveryRepository protocol, so swapping
the JSON store for Postgres is a single call-site change.
"""

from auto_search.db.repository import DiscoveryRepository, JsonFileRepository

__all__ = ["DiscoveryRepository", "JsonFileRepository"]
