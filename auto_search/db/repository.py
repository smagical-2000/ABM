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

from auto_search.job_stacking import PARK_TTL_DAYS
from auto_search.models import CompanyCandidate, RawSignal
from auto_search.normalize import normalize_keyword as _keyword_key
from auto_search.normalize import normalize_linkedin_url

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

    def add_signal(self, company_key: str, signal: RawSignal) -> bool:
        """Append one signal to an EXISTING company, without touching its verdict.

        Append-only: used when a new engager/signal arrives for a company that's
        already been qualified, so we record the provenance without re-paying the
        qualifier or resetting the review decision. Dedups on
        source_external_id. Returns True if the signal was added, False if the
        company doesn't exist or the signal was already stored.
        """
        ...

    def update_signal(self, company_key: str, signal: RawSignal) -> bool:
        """Upsert a signal on an EXISTING company by source_external_id.

        Like add_signal, but REPLACES the stored signal when one with the same
        source_external_id exists (else appends). Used to enrich a contact in
        place — e.g. after an ICP-qualified company's engager is enriched, its
        scrape-level signal is overwritten with the enriched company/title.
        Returns True if updated/added, False if the company doesn't exist.
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

    def enter_needs_review(self, company_key: str) -> dict | None:
        """Demote a stale qualified lead into the needs-review queue (the
        lifecycle sweep). Flips icp_status qualified -> needs_review only when it
        is currently qualified (idempotent); review_status stays pending. Returns
        the updated row, or None if not stored / not currently qualified.
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

    def cleanup_stale_runs(self) -> int:
        """Mark any still-'running' rows as failed; returns how many.

        Run state (the live coroutine) is in-memory, so on a restart/crash a
        run row can be left 'running' forever with no process behind it. Calling
        this at startup clears those orphans so the panel can't show a phantom
        in-progress run with dead pause/cancel controls.
        """
        ...

    def recent_decisions(self, *, limit: int = 20) -> list[dict]:
        """Most recently decided companies (any verdict) for the run log."""
        ...

    def replace_abm_targets(self, targets: list[dict]) -> int:
        """Replace the stored ABM target list wholesale; return rows stored."""
        ...

    def abm_targets(self) -> list[dict]:
        """All stored ABM target rows (for building the match index)."""
        ...

    def abm_targets_summary(self) -> dict:
        """Counts for the UI: total, by segment, last upload time."""
        ...

    def founder_profiles(self) -> list[dict]:
        """Cached founder LinkedIn profiles for warm-intro matching."""
        ...

    def replace_founder_profiles(self, profiles: list[dict]) -> int:
        """Replace the cached founder profiles wholesale; return rows stored."""
        ...

    def news_urls(self) -> list[str]:
        """URLs already stored — lets the news runner enrich only NEW articles."""
        ...

    def save_news_items(self, items: list[dict]) -> int:
        """Upsert news items (keyed by url); return how many were new."""
        ...

    def news_items(self, *, topics: tuple[str, ...] | None = None,
                   days: int | None = None, limit: int = 200) -> list[dict]:
        """Recent relevant news, newest first; optional topic / day-window filter."""
        ...

    def social_targets(self) -> list[dict]:
        """Monitored LinkedIn accounts (own + competitors)."""
        ...

    def upsert_social_target(self, target: dict) -> dict:
        """Add or update a monitored account (keyed by normalized URL)."""
        ...

    def delete_social_target(self, linkedin_url: str) -> bool:
        """Remove a monitored account; True if one was removed."""
        ...

    def event_keywords(self) -> list[dict]:
        """Tracked event/conference keywords to search posts for."""
        ...

    def upsert_event_keyword(self, keyword: dict) -> dict:
        """Add or update an event keyword (keyed case/quote-insensitively)."""
        ...

    def delete_event_keyword(self, keyword: str) -> bool:
        """Remove an event keyword; True if one was removed."""
        ...

    # -- stacking watch ledger (jobs cost gate) --

    def upsert_parked(self, record: dict) -> dict:
        """Add/refresh a stacking-parked company in the watch ledger.

        Keyed by `company_key`. Stamps `first_parked_at` on insert and
        `last_seen_at` on every call, so the watch list can show "seen N days
        ago" and prune companies that stopped hiring. Idempotent.
        """
        ...

    def parked_companies(self) -> list[dict]:
        """The watch list: companies the jobs gate parked (a single standard
        RCM posting), most-recently-seen first.

        Self-correcting: excludes any company now decided in the discovery store
        (it graduated/qualified by any path) and drops entries older than the
        TTL, so a parked company that later qualifies or goes quiet falls off
        without an explicit delete.
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
            existing["signals"].append(_signal_to_dict(sig))
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

    def add_signal(self, company_key: str, signal: RawSignal) -> bool:
        row = self._store.get(company_key)
        if row is None:
            return False
        signals = row.setdefault("signals", [])
        if any(s["source_external_id"] == signal.source_external_id for s in signals):
            return False
        signals.append(_signal_to_dict(signal))
        self._flush()
        return True

    def update_signal(self, company_key: str, signal: RawSignal) -> bool:
        row = self._store.get(company_key)
        if row is None:
            return False
        signals = row.setdefault("signals", [])
        new_row = _signal_to_dict(signal)
        for i, s in enumerate(signals):
            if s["source_external_id"] == signal.source_external_id:
                signals[i] = new_row
                break
        else:
            signals.append(new_row)
        self._flush()
        return True

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

    def enter_needs_review(self, company_key: str) -> dict | None:
        row = self._store.get(company_key)
        if row is None or row.get("icp_status") != "qualified":
            return None
        row["icp_status"] = "needs_review"
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

    def cleanup_stale_runs(self) -> int:
        runs = self._load_runs()
        n = 0
        for r in runs:
            if r.get("status") == "running":
                r["status"] = "failed"
                r["error_message"] = "orphaned by restart"
                r["finished_at"] = datetime.now(UTC).isoformat()
                n += 1
        if n:
            self._flush_runs(runs)
        return n

    def recent_decisions(self, *, limit: int = 20) -> list[dict]:
        rows = [r for r in self._store.values() if r.get("qualified_at")]
        rows.sort(key=lambda r: r.get("qualified_at") or "", reverse=True)
        out: list[dict] = []
        for r in rows[:limit]:
            sigs = r.get("signals") or []
            top = sigs[0] if sigs else {}
            payload = top.get("payload") or {}
            out.append({
                "company_key": r.get("normalized_name"),
                "name": r.get("display_name"),
                "status": r.get("icp_status"),
                "segment": r.get("segment"),
                "at": r.get("qualified_at"),
                "signal_type": top.get("signal_type"),
                "signal_summary": top.get("summary") or payload.get("role"),
            })
        return out

    # ── ABM target list (sidecar file, mirrors the runs sidecar) ──────

    def _abm_path(self) -> Path:
        return self._path.with_name("abm_targets.json")

    def _abm_doc(self) -> dict:
        p = self._abm_path()
        if not p.exists():
            return {"uploaded_at": None, "targets": []}
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            return {"uploaded_at": None, "targets": []}

    def replace_abm_targets(self, targets: list[dict]) -> int:
        p = self._abm_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        doc = {"uploaded_at": datetime.now(UTC).isoformat(), "targets": targets}
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(doc, default=str))
        tmp.replace(p)
        return len(targets)

    def abm_targets(self) -> list[dict]:
        return self._abm_doc().get("targets", [])

    def abm_targets_summary(self) -> dict:
        doc = self._abm_doc()
        targets = doc.get("targets", [])
        by_segment: dict[str, int] = {}
        for t in targets:
            seg = t.get("segment") or t.get("source_sheet") or "Other"
            by_segment[seg] = by_segment.get(seg, 0) + 1
        return {
            "total": len(targets),
            "by_segment": dict(sorted(by_segment.items(), key=lambda kv: -kv[1])),
            "uploaded_at": doc.get("uploaded_at"),
        }

    # ── market-intelligence news (sidecar file) ──────────────────────

    def _news_path(self) -> Path:
        return self._path.with_name("news_items.json")

    def _news_store(self) -> dict[str, dict]:
        p = self._news_path()
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            return {}

    def news_urls(self) -> list[str]:
        return list(self._news_store().keys())

    def save_news_items(self, items: list[dict]) -> int:
        store = self._news_store()
        new = 0
        for it in items:
            url = it.get("url")
            if not url:
                continue
            if url not in store:
                new += 1
            store[url] = it
        # Cap at the 500 most recent so the sidecar can't grow without bound.
        if len(store) > 500:
            kept = sorted(store.values(),
                          key=lambda r: r.get("published_at") or r.get("fetched_at") or "",
                          reverse=True)[:500]
            store = {r["url"]: r for r in kept}
        p = self._news_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(store, default=str))
        tmp.replace(p)
        return new

    def news_items(self, *, topics=None, days=None, limit=200) -> list[dict]:
        rows = [r for r in self._news_store().values() if r.get("relevant", True)]
        if topics:
            tset = set(topics)
            rows = [r for r in rows if r.get("topic") in tset]
        if days:
            cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
            rows = [r for r in rows
                    if (r.get("published_at") or r.get("fetched_at") or "") >= cutoff]
        rows.sort(key=lambda r: (r.get("get_behind") or 0,
                                 r.get("published_at") or r.get("fetched_at") or ""), reverse=True)
        return rows[:limit]

    # ── monitored social accounts (sidecar file) ─────────────────────

    def _social_path(self) -> Path:
        return self._path.with_name("social_targets.json")

    def _social_targets(self) -> list[dict]:
        p = self._social_path()
        if not p.exists():
            return []
        try:
            return json.loads(p.read_text()).get("targets", [])
        except json.JSONDecodeError:
            return []

    def _write_social(self, targets: list[dict]) -> None:
        p = self._social_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps({"targets": targets}, default=str))
        tmp.replace(p)

    def social_targets(self) -> list[dict]:
        return self._social_targets()

    # ── founder profiles (warm intros, sidecar file) ─────────────────

    def _founders_path(self) -> Path:
        return self._path.with_name("founder_profiles.json")

    def founder_profiles(self) -> list[dict]:
        p = self._founders_path()
        if not p.exists():
            return []
        try:
            return json.loads(p.read_text()).get("profiles", [])
        except json.JSONDecodeError:
            return []

    def replace_founder_profiles(self, profiles: list[dict]) -> int:
        p = self._founders_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps({"profiles": profiles}, default=str))
        tmp.replace(p)
        return len(profiles)

    def upsert_social_target(self, target: dict) -> dict:
        targets = self._social_targets()
        key = normalize_linkedin_url(target.get("linkedin_url"))
        row = next((t for t in targets
                    if normalize_linkedin_url(t.get("linkedin_url")) == key), None)
        if row is None:
            row = {"linkedin_url": target["linkedin_url"],
                   "created_at": datetime.now(UTC).isoformat()}
            targets.append(row)
        row["label"] = target.get("label", row.get("label"))
        row["kind"] = target.get("kind", row.get("kind", "competitor"))
        row["active"] = target.get("active", row.get("active", True))
        self._write_social(targets)
        return row

    def delete_social_target(self, linkedin_url: str) -> bool:
        targets = self._social_targets()
        key = normalize_linkedin_url(linkedin_url)
        kept = [t for t in targets
                if normalize_linkedin_url(t.get("linkedin_url")) != key]
        if len(kept) == len(targets):
            return False
        self._write_social(kept)
        return True

    # -- event keywords (sidecar, mirrors social targets) --

    def _keywords_path(self) -> Path:
        return self._path.with_name("event_keywords.json")

    def _event_keywords(self) -> list[dict]:
        p = self._keywords_path()
        if not p.exists():
            return []
        try:
            return json.loads(p.read_text()).get("keywords", [])
        except json.JSONDecodeError:
            return []

    def event_keywords(self) -> list[dict]:
        return self._event_keywords()

    def upsert_event_keyword(self, keyword: dict) -> dict:
        rows = self._event_keywords()
        key = _keyword_key(keyword.get("keyword"))
        row = next((k for k in rows if _keyword_key(k.get("keyword")) == key), None)
        if row is None:
            row = {"keyword": keyword["keyword"].strip(),
                   "created_at": datetime.now(UTC).isoformat()}
            rows.append(row)
        row["label"] = keyword.get("label", row.get("label"))
        row["active"] = keyword.get("active", row.get("active", True))
        p = self._keywords_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps({"keywords": rows}, default=str))
        tmp.replace(p)
        return row

    def delete_event_keyword(self, keyword: str) -> bool:
        rows = self._event_keywords()
        key = _keyword_key(keyword)
        kept = [k for k in rows if _keyword_key(k.get("keyword")) != key]
        if len(kept) == len(rows):
            return False
        p = self._keywords_path()
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps({"keywords": kept}, default=str))
        tmp.replace(p)
        return True

    # -- stacking watch ledger (sidecar, mirrors social targets) --

    def _parked_path(self) -> Path:
        return self._path.with_name("parked_companies.json")

    def _parked(self) -> list[dict]:
        p = self._parked_path()
        if not p.exists():
            return []
        try:
            return json.loads(p.read_text()).get("companies", [])
        except json.JSONDecodeError:
            return []

    def _write_parked(self, rows: list[dict]) -> None:
        p = self._parked_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps({"companies": rows}, default=str))
        tmp.replace(p)

    def upsert_parked(self, record: dict) -> dict:
        rows = self._parked()
        key = record["company_key"]
        now = datetime.now(UTC).isoformat()
        row = next((r for r in rows if r.get("company_key") == key), None)
        if row is None:
            row = {"company_key": key, "first_parked_at": now}
            rows.append(row)
        row.update({k: v for k, v in record.items() if k != "company_key"})
        row["last_seen_at"] = now
        self._write_parked(rows)
        return row

    def parked_companies(self) -> list[dict]:
        rows = self._parked()
        # ISO-8601 UTC strings compare chronologically, so a lexical cutoff works.
        cutoff = (datetime.now(UTC) - timedelta(days=PARK_TTL_DAYS)).isoformat()
        fresh = [
            r for r in rows
            if (r.get("last_seen_at") or "") >= cutoff
            and not self.already_qualified(r.get("company_key", ""))
        ]
        if len(fresh) != len(rows):       # prune graduated/stale entries in place
            self._write_parked(fresh)
        fresh.sort(key=lambda r: r.get("last_seen_at") or "", reverse=True)
        return fresh

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


def _signal_to_dict(sig: RawSignal) -> dict:
    """The stored JSON shape for one signal — shared by save_candidate +
    add_signal so the persisted record never drifts between the two paths."""
    return {
        "source": sig.source,
        "signal_type": sig.signal_type,
        "source_external_id": sig.source_external_id,
        "summary": sig.summary,
        "signal_strength": sig.signal_strength,
        "observed_at": sig.observed_at.isoformat(),
        "payload": sig.payload,
    }


# Signal-summary text lives on RawSignal.summary (models.py) so JSON and
# Postgres repos render the 'why discovered' line identically.
