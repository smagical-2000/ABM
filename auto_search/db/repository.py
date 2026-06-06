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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from auto_search.models import CompanyCandidate

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

    def panel(self, statuses: tuple[str, ...] = ("qualified",)) -> list[dict]:
        """Return companies for the review panel, most recent first.

        Only companies whose verdict is in `statuses` surface here — by
        default just `qualified`. This is what the UI reads; disqualified /
        error rows stay in storage (as the don't-reprocess ledger) but never
        reach the panel.
        """
        ...

    def get(self, company_key: str) -> dict | None:
        """Return one stored company row (for the detail drawer), or None."""
        ...

    def set_review(
        self, company_key: str, review_status: str, *, reason: str | None = None,
    ) -> dict | None:
        """Record Galyna's decision (promoted / rejected / deferred / pending).

        Returns the updated row, or None if the company isn't stored. Stamps
        reviewed_at; stores rejection reason; stamps promoted_at on promote.
        """
        ...

    def delete(self, keys: list[str] | None) -> int:
        """Delete companies (and their signals) by normalized key.

        `keys=None` wipes the entire discovery store (clean slate). Returns the
        number of company rows removed. Deleting a company also removes it from
        the dedup ledger, so it can be re-discovered on a later run.
        """
        ...

    # -- run heartbeat (powers the live "processing" marker) --

    def start_run(self, source: str) -> int:
        """Open a 'running' row for a discovery run; returns its id."""
        ...

    def update_run(self, run_id: int, **counts: int) -> None:
        """Update live counts on a running row (absolute values).

        Accepts any of planned/rows_fetched/new_companies/signals_added/
        companies_qualified. `planned` is the run's qualification denominator,
        set once the unique-company set is known, so the UI can show progress %.
        """
        ...

    def finish_run(
        self, run_id: int, *, status: str = "success", error: str | None = None,
    ) -> None:
        """Close a run row (status success/failed + finished_at)."""
        ...

    def active_runs(self, *, max_age_minutes: int = 15) -> list[dict]:
        """Recently-started runs still in progress (drives the UI marker)."""
        ...

    def recent_decisions(self, *, limit: int = 12) -> list[dict]:
        """Most recently decided companies (any verdict) for the live feed."""
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
        now = datetime.now(UTC).isoformat()

        existing = self._store.get(key)
        if existing is None:
            existing = {
                "normalized_name": key,
                "display_name": candidate.company_name,
                "first_seen_at": now,
                # Human workflow state, separate from the machine icp_status.
                # 'pending' until Galyna promotes / rejects / defers. Set only
                # on first insert so re-qualifying never resets her decision.
                "review_status": "pending",
                "signals": [],
            }
            self._store[key] = existing

        # Upsert the verdict (newest qualification wins). to_status() keeps
        # operational errors out of the genuine review/disqualified queues.
        existing.update({
            "display_name": candidate.company_name,
            "domain": q.domain,                 # for domain-first promotion match
            "icp_status": q.to_status(),
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
                "summary": sig.summary,
                "signal_strength": sig.signal_strength,
                "observed_at": sig.observed_at.isoformat(),
                "payload": sig.payload,
            })
            seen_ids.add(sig.source_external_id)

        self._flush()
        return key

    # Statuses that count as "already decided" — re-seeing the company
    # should NOT re-trigger a Claude call. `error` is intentionally absent:
    # an operational failure should be retried on the next run.
    _DECIDED_STATUSES = frozenset({"qualified", "needs_review", "disqualified"})

    def already_qualified(self, company_key: str) -> bool:
        row = self._store.get(company_key)
        return bool(row) and row.get("icp_status") in self._DECIDED_STATUSES

    def panel(self, statuses: tuple[str, ...] = ("qualified",)) -> list[dict]:
        rows = [r for r in self._store.values() if r.get("icp_status") in statuses]
        # Most recently qualified first — that's the order the UI wants.
        rows.sort(key=lambda r: r.get("qualified_at") or "", reverse=True)
        return rows

    def stats(self) -> dict[str, int]:
        """Counts by verdict status — for the runner summary + ops view."""
        counts: dict[str, int] = {}
        for r in self._store.values():
            counts[r.get("icp_status", "pending")] = (
                counts.get(r.get("icp_status", "pending"), 0) + 1
            )
        counts["total"] = len(self._store)
        return counts

    def get(self, company_key: str) -> dict | None:
        return self._store.get(company_key)

    _REVIEW_STATUSES = frozenset({"pending", "promoted", "rejected", "deferred"})

    def set_review(
        self, company_key: str, review_status: str, *, reason: str | None = None,
    ) -> dict | None:
        if review_status not in self._REVIEW_STATUSES:
            raise ValueError(
                f"invalid review_status {review_status!r}; "
                f"expected one of {sorted(self._REVIEW_STATUSES)}"
            )
        row = self._store.get(company_key)
        if row is None:
            return None
        now = datetime.now(UTC).isoformat()
        row["review_status"] = review_status
        row["reviewed_at"] = now
        if review_status == "promoted":
            row["promoted_at"] = now
        if review_status == "rejected":
            row["rejection_reason"] = reason
        self._flush()
        return row

    def delete(self, keys: list[str] | None) -> int:
        """Remove companies by key (signals are nested, so they go too).
        `keys=None` clears the whole store."""
        if keys is None:
            n = len(self._store)
            self._store = {}
            self._flush()
            return n
        n = 0
        for k in keys:
            if k in self._store:
                del self._store[k]
                n += 1
        if n:
            self._flush()
        return n

    # -- run heartbeat (file-backed so the API process sees the runner's runs) --

    def _runs_path(self) -> Path:
        return self._path.with_name("discovery_runs.json")

    def _load_runs(self) -> list[dict]:
        p = self._runs_path()
        if not p.exists():
            return []
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            return []

    def _flush_runs(self, runs: list[dict]) -> None:
        p = self._runs_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(runs, indent=2, default=str))
        tmp.replace(p)

    def start_run(self, source: str) -> int:
        runs = self._load_runs()
        run_id = max((r["id"] for r in runs), default=0) + 1
        runs.append({
            "id": run_id, "source": source, "status": "running",
            "started_at": datetime.now(UTC).isoformat(), "finished_at": None,
            "planned": 0, "rows_fetched": 0, "new_companies": 0,
            "signals_added": 0, "companies_qualified": 0, "error_message": None,
        })
        self._flush_runs(runs[-200:])  # cap history
        return run_id

    def update_run(self, run_id: int, **counts: int) -> None:
        runs = self._load_runs()
        for r in runs:
            if r["id"] == run_id:
                for c in ("planned", "rows_fetched", "new_companies",
                          "signals_added", "companies_qualified"):
                    if counts.get(c) is not None:
                        r[c] = counts[c]
        self._flush_runs(runs)

    def finish_run(
        self, run_id: int, *, status: str = "success", error: str | None = None,
    ) -> None:
        runs = self._load_runs()
        for r in runs:
            if r["id"] == run_id:
                r["status"] = status
                r["error_message"] = error
                r["finished_at"] = datetime.now(UTC).isoformat()
        self._flush_runs(runs)

    def active_runs(self, *, max_age_minutes: int = 15) -> list[dict]:
        cutoff = datetime.now(UTC) - timedelta(minutes=max_age_minutes)
        now = datetime.now(UTC)
        out: list[dict] = []
        for r in self._load_runs():
            if r.get("status") != "running":
                continue
            try:
                started = datetime.fromisoformat(r["started_at"])
            except (ValueError, KeyError):
                continue
            if started < cutoff:
                continue
            out.append({
                "source": r["source"], "started_at": r["started_at"],
                "planned": r.get("planned", 0),
                "rows_fetched": r.get("rows_fetched", 0),
                "new_companies": r.get("new_companies", 0),
                "signals_added": r.get("signals_added", 0),
                "companies_qualified": r.get("companies_qualified", 0),
                "elapsed_seconds": int((now - started).total_seconds()),
            })
        out.sort(key=lambda x: x["started_at"], reverse=True)
        return out

    def recent_decisions(self, *, limit: int = 12) -> list[dict]:
        rows = [r for r in self._store.values() if r.get("qualified_at")]
        rows.sort(key=lambda r: r.get("qualified_at") or "", reverse=True)
        return [
            {"name": r.get("display_name"), "status": r.get("icp_status"),
             "segment": r.get("segment"), "at": r.get("qualified_at")}
            for r in rows[:limit]
        ]

    # -- internals --

    def _load(self) -> dict[str, dict]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text())
        except json.JSONDecodeError:
            # Never silently wipe data: preserve the corrupt file for forensics
            # before starting fresh, so a bad write can't erase real history.
            backup = self._path.with_suffix(self._path.suffix + ".corrupt")
            try:
                self._path.replace(backup)
                logger.error("corrupt store at %s — moved to %s, starting empty",
                             self._path, backup)
            except OSError:
                logger.error("corrupt store at %s — starting empty", self._path)
            return {}

    def _flush(self) -> None:
        """Write atomically: dump to a temp file then rename, so a crash
        mid-write can't truncate the real store into corrupt JSON."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._store, indent=2, default=str))
        tmp.replace(self._path)


# ── helpers ───────────────────────────────────────────────────────────


# Signal-summary text lives on RawSignal.summary (models.py) so JSON and
# Postgres repos render the 'why discovered' line identically.
