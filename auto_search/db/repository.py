"""Persistence for discovery results — interface + a no-Postgres implementation.

Why an interface
----------------
The pipeline shouldn't know whether candidates land in Postgres, a JSON file,
or a test double. It calls `repo.save_candidate(...)` and moves on. That keeps
the pipeline pure and lets us run end-to-end TODAY (JSON file) and swap to
Postgres the day Railway is connected — without touching pipeline code.

What gets stored (and what doesn't)
-----------------------------------
We persist the VERDICT + PROVENANCE, never the raw firehose:
  • discovery_companies — one row per unique company + its ICP verdict
  • discovery_signals   — the signals that put it in the funnel (the "why")
We do NOT store the full WARN dataset, disqualified-company essays, or raw
Claude traces (those go to files with a TTL).

Dedup is enforced at save time, mirroring the DB UNIQUE constraints:
  • a company seen again updates the existing row, never inserts a duplicate
  • a signal seen again (same source + external_id) is skipped
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from auto_search.pipeline import CompanyCandidate

logger = logging.getLogger(__name__)


# ── interface ─────────────────────────────────────────────────────────


class DiscoveryRepository(Protocol):
    """Storage contract for the discovery pipeline.

    Implementations must be idempotent: saving the same candidate twice
    leaves the store in the same state as saving it once (upsert semantics).
    """

    def save_candidate(self, candidate: CompanyCandidate) -> str:
        """Persist a qualified/evaluated company + its signals.

        Returns the company's stable id (so callers can link/log). Must
        upsert on the normalized company name and skip duplicate signals.
        """
        ...

    def already_qualified(self, company_key: str) -> bool:
        """True if this company already has a non-pending verdict.

        The pipeline can call this BEFORE qualifying to skip the Claude
        call entirely for companies we've already decided on — the
        ultimate "don't reprocess" guard across runs.
        """
        ...


# ── JSON-file implementation (works now, zero infra) ──────────────────


class JsonFileRepository:
    """Reference implementation backed by a single JSON file.

    Good for local runs and tests before Postgres exists. The on-disk shape
    mirrors the SQL schema (companies keyed by normalized_name, each holding
    its signals) so the migration to Postgres is a mechanical 1:1 mapping.

    NOT for production concurrency — it rewrites the whole file on each save.
    That's fine for a nightly single-writer cron at current volume.
    """

    def __init__(self, path: str | Path = "./data/discovery_store.json") -> None:
        self._path = Path(path)
        self._store: dict[str, dict] = self._load()

    # -- public (DiscoveryRepository) --

    def save_candidate(self, candidate: CompanyCandidate) -> str:
        key = candidate.company_key
        q = candidate.qualification
        now = datetime.now(timezone.utc).isoformat()

        existing = self._store.get(key)
        if existing is None:
            existing = {
                "normalized_name": key,
                "display_name": candidate.company_name,
                "first_seen_at": now,
                "signals": [],
            }
            self._store[key] = existing

        # Upsert the verdict (newest qualification wins).
        existing.update({
            "display_name": candidate.company_name,
            "icp_status": _icp_status(q.qualified, q.needs_human_review),
            "segment": q.segment,
            "sub_segment": q.sub_segment,
            "company_type": q.company_type,
            "approximate_employees": q.approximate_employees,
            "confidence": q.confidence,
            "reasoning": q.reasoning,
            "evidence_url": q.evidence_url,
            "decided_by": q.decided_by,
            "qualified_at": now,
        })

        # Lean firmo from the representative signal.
        rep_payload = candidate.primary_signal.payload
        existing["hq_state"] = rep_payload.get("state")
        existing["hq_city"] = rep_payload.get("city")

        # Append signals, skipping ones already stored (signal-level dedup).
        seen_ids = {s["source_external_id"] for s in existing["signals"]}
        for sig in candidate.signals:
            if sig.source_external_id in seen_ids:
                continue
            existing["signals"].append({
                "source": sig.source,
                "signal_type": sig.signal_type,
                "source_external_id": sig.source_external_id,
                "summary": _signal_summary(sig),
                "signal_strength": sig.signal_strength,
                "observed_at": sig.observed_at.isoformat(),
                "payload": sig.payload,
            })
            seen_ids.add(sig.source_external_id)

        self._flush()
        return key

    def already_qualified(self, company_key: str) -> bool:
        row = self._store.get(company_key)
        return bool(row) and row.get("icp_status", "pending") != "pending"

    # -- internals --

    def _load(self) -> dict[str, dict]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text())
        except json.JSONDecodeError:
            logger.warning("corrupt store at %s — starting empty", self._path)
            return {}

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._store, indent=2, default=str))


# ── helpers ───────────────────────────────────────────────────────────


def _icp_status(qualified: bool, needs_review: bool) -> str:
    """Collapse the two booleans into the schema's single status enum."""
    if needs_review:
        return "needs_review"
    return "qualified" if qualified else "disqualified"


def _signal_summary(sig) -> str:
    """Human one-liner for the UI's 'why is this here' list."""
    p = sig.payload
    if sig.signal_type == "layoff":
        n = p.get("laid_off_count")
        where = p.get("city") or p.get("state") or ""
        head = f"{n} laid off" if n else "layoff"
        return f"{head}{f' in {where}' if where else ''}".strip()
    return sig.signal_type


# NOTE: PostgresRepository(asyncpg) implements the same protocol against
# schema.sql. Deferred until Railway Postgres is connected — the JSON impl
# above keeps the pipeline runnable and tested until then. The swap is a
# one-line change at the call site (which repo we instantiate).
