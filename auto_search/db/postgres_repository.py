"""PostgresRepository — the production storage for discovery.

Implements the same (synchronous) DiscoveryRepository protocol as
JsonFileRepository, so the pipeline, services, and UI don't change when you
switch — only which repo `get_repository()` returns.

Uses psycopg3 (sync) deliberately: the protocol is sync, the discovery runner
calls it inside an async loop with brief local writes, and FastAPI runs sync
handlers in a threadpool. A sync driver keeps one interface across both repos
with no async/await leaking into the pipeline or service layers.

The two dedup layers are enforced by the DB (schema.sql UNIQUE constraints),
not app code:
  • discovery_signals (source, source_external_id)  — same event never twice
  • discovery_companies (normalized_name)           — one row per company

Connection: pass a DSN or set DATABASE_URL (e.g. postgresql://localhost/abm_discovery
locally, or the Railway URL in production).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from psycopg.rows import dict_row
from psycopg.types.json import Json
from psycopg_pool import ConnectionPool

from auto_search.models import CompanyCandidate, RawSignal
from auto_search.normalize import normalize_keyword as _keyword_key
from auto_search.normalize import normalize_linkedin_url

logger = logging.getLogger(__name__)

# Statuses that count as "already decided" — re-seeing the company must NOT
# re-trigger Claude. 'error' is intentionally absent so failures retry.
_DECIDED_STATUSES = ("qualified", "needs_review", "disqualified")
_REVIEW_STATUSES = frozenset({"pending", "promoted", "rejected", "deferred"})


class PostgresRepository:
    """Discovery storage backed by Postgres (psycopg3, pooled, sync)."""

    def __init__(self, dsn: str | None = None) -> None:
        dsn = dsn or os.getenv("DATABASE_URL")
        if not dsn:
            raise RuntimeError(
                "DATABASE_URL not set (e.g. postgresql://localhost/abm_discovery)"
            )
        # Small pool — the runner is single-writer; the API does light reads.
        self._pool = ConnectionPool(dsn, min_size=1, max_size=5, open=True,
                                    kwargs={"row_factory": dict_row})

    def close(self) -> None:
        self._pool.close()

    def ensure_schema(self) -> None:
        """Create the tables if they don't exist (idempotent).

        schema.sql uses CREATE TABLE / INDEX IF NOT EXISTS, so running it on
        every boot is safe. This makes a fresh deploy (e.g. a new Railway
        Postgres) self-initialising — no manual migration step.
        """
        sql = (Path(__file__).resolve().parent / "schema.sql").read_text()
        with self._pool.connection() as conn:
            conn.execute(sql)
        logger.info("schema ensured")

    # ── writes ─────────────────────────────────────────────────────────

    def save_candidate(self, candidate: CompanyCandidate) -> str:
        key = candidate.company_key
        q = candidate.qualification
        rep = candidate.primary_signal.payload
        now = datetime.now(UTC)

        with self._pool.connection() as conn, conn.transaction():
            # Upsert the company. review_status is set only on INSERT (never
            # reset by a re-qualification — preserves Galyna's decision).
            conn.execute(
                """
                INSERT INTO discovery_companies (
                    normalized_name, display_name, domain, icp_status,
                    segment, sub_segment, company_type, approximate_employees,
                    confidence, reasoning, evidence_url, decided_by,
                    hq_state, hq_city, first_seen_at, qualified_at
                ) VALUES (
                    %(key)s, %(name)s, %(domain)s, %(icp_status)s,
                    %(segment)s, %(sub_segment)s, %(company_type)s, %(employees)s,
                    %(confidence)s, %(reasoning)s, %(evidence_url)s, %(decided_by)s,
                    %(hq_state)s, %(hq_city)s, %(now)s, %(now)s
                )
                ON CONFLICT (normalized_name) DO UPDATE SET
                    display_name = EXCLUDED.display_name,
                    domain = EXCLUDED.domain,
                    icp_status = EXCLUDED.icp_status,
                    segment = EXCLUDED.segment,
                    sub_segment = EXCLUDED.sub_segment,
                    company_type = EXCLUDED.company_type,
                    approximate_employees = EXCLUDED.approximate_employees,
                    confidence = EXCLUDED.confidence,
                    reasoning = EXCLUDED.reasoning,
                    evidence_url = EXCLUDED.evidence_url,
                    decided_by = EXCLUDED.decided_by,
                    qualified_at = EXCLUDED.qualified_at
                """,
                {
                    "key": key, "name": candidate.company_name,
                    "domain": q.domain, "icp_status": q.to_status(),
                    "segment": q.segment, "sub_segment": q.sub_segment,
                    "company_type": q.company_type,
                    "employees": q.approximate_employees,
                    "confidence": q.confidence, "reasoning": q.reasoning,
                    "evidence_url": q.evidence_url, "decided_by": q.decided_by,
                    "hq_state": rep.get("state"), "hq_city": rep.get("city"),
                    "now": now,
                },
            )
            company_id = conn.execute(
                "SELECT id FROM discovery_companies WHERE normalized_name = %s",
                (key,),
            ).fetchone()["id"]

            # Insert signals; dedup on (source, source_external_id).
            for sig in candidate.signals:
                conn.execute(
                    """
                    INSERT INTO discovery_signals (
                        company_id, source, signal_type, source_external_id,
                        summary, signal_strength, observed_at, payload
                    ) VALUES (
                        %(cid)s, %(source)s, %(type)s, %(ext)s,
                        %(summary)s, %(strength)s, %(observed)s, %(payload)s
                    )
                    ON CONFLICT (source, source_external_id) DO NOTHING
                    """,
                    {
                        "cid": company_id, "source": sig.source,
                        "type": sig.signal_type,
                        "ext": sig.source_external_id,
                        "summary": sig.summary,
                        "strength": sig.signal_strength,
                        "observed": sig.observed_at,
                        "payload": json.dumps(sig.payload, default=str),
                    },
                )
        return key

    def add_signal(self, company_key: str, signal: RawSignal) -> bool:
        """Append one signal to an existing company; dedup on
        (source, source_external_id). No verdict touched. See protocol docstring."""
        with self._pool.connection() as conn, conn.transaction():
            company = conn.execute(
                "SELECT id FROM discovery_companies WHERE normalized_name = %s",
                (company_key,),
            ).fetchone()
            if not company:
                return False
            inserted = conn.execute(
                """
                INSERT INTO discovery_signals (
                    company_id, source, signal_type, source_external_id,
                    summary, signal_strength, observed_at, payload
                ) VALUES (
                    %(cid)s, %(source)s, %(type)s, %(ext)s,
                    %(summary)s, %(strength)s, %(observed)s, %(payload)s
                )
                ON CONFLICT (source, source_external_id) DO NOTHING
                RETURNING id
                """,
                {
                    "cid": company["id"], "source": signal.source,
                    "type": signal.signal_type, "ext": signal.source_external_id,
                    "summary": signal.summary, "strength": signal.signal_strength,
                    "observed": signal.observed_at,
                    "payload": json.dumps(signal.payload, default=str),
                },
            ).fetchone()
        return inserted is not None

    def update_signal(self, company_key: str, signal: RawSignal) -> bool:
        """Upsert a signal by (source, source_external_id) — replaces payload/
        summary when it exists, else inserts. See protocol docstring."""
        with self._pool.connection() as conn, conn.transaction():
            company = conn.execute(
                "SELECT id FROM discovery_companies WHERE normalized_name = %s",
                (company_key,),
            ).fetchone()
            if not company:
                return False
            conn.execute(
                """
                INSERT INTO discovery_signals (
                    company_id, source, signal_type, source_external_id,
                    summary, signal_strength, observed_at, payload
                ) VALUES (
                    %(cid)s, %(source)s, %(type)s, %(ext)s,
                    %(summary)s, %(strength)s, %(observed)s, %(payload)s
                )
                ON CONFLICT (source, source_external_id) DO UPDATE SET
                    signal_type = EXCLUDED.signal_type,
                    summary = EXCLUDED.summary,
                    signal_strength = EXCLUDED.signal_strength,
                    observed_at = EXCLUDED.observed_at,
                    payload = EXCLUDED.payload
                """,
                {
                    "cid": company["id"], "source": signal.source,
                    "type": signal.signal_type, "ext": signal.source_external_id,
                    "summary": signal.summary, "strength": signal.signal_strength,
                    "observed": signal.observed_at,
                    "payload": json.dumps(signal.payload, default=str),
                },
            )
        return True

    def set_review(
        self, company_key: str, review_status: str, *, reason: str | None = None,
    ) -> dict | None:
        if review_status not in _REVIEW_STATUSES:
            raise ValueError(
                f"invalid review_status {review_status!r}; "
                f"expected one of {sorted(_REVIEW_STATUSES)}"
            )
        now = datetime.now(UTC)
        with self._pool.connection() as conn:
            row = conn.execute(
                """
                UPDATE discovery_companies
                   SET review_status = %(status)s,
                       reviewed_at = %(now)s,
                       promoted_at = CASE WHEN %(status)s = 'promoted'
                                          THEN %(now)s ELSE promoted_at END,
                       rejection_reason = CASE WHEN %(status)s = 'rejected'
                                               THEN %(reason)s ELSE rejection_reason END
                 WHERE normalized_name = %(key)s
             RETURNING normalized_name
                """,
                {"status": review_status, "now": now, "reason": reason,
                 "key": company_key},
            ).fetchone()
        return self.get(company_key) if row else None

    # ── reads ──────────────────────────────────────────────────────────

    def already_qualified(self, company_key: str) -> bool:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT icp_status FROM discovery_companies WHERE normalized_name = %s",
                (company_key,),
            ).fetchone()
        return bool(row) and row["icp_status"] in _DECIDED_STATUSES

    def get(self, company_key: str) -> dict | None:
        with self._pool.connection() as conn:
            company = conn.execute(
                "SELECT * FROM discovery_companies WHERE normalized_name = %s",
                (company_key,),
            ).fetchone()
            if not company:
                return None
            signals = conn.execute(
                """SELECT source, signal_type, summary, signal_strength, observed_at,
                          payload
                     FROM discovery_signals WHERE company_id = %s
                    ORDER BY observed_at DESC""",
                (company["id"],),
            ).fetchall()
        return _to_row(company, signals)

    def panel(self, statuses: tuple[str, ...] = ("qualified",)) -> list[dict]:
        with self._pool.connection() as conn:
            companies = conn.execute(
                """SELECT * FROM discovery_companies
                    WHERE icp_status = ANY(%s)
                    ORDER BY qualified_at DESC NULLS LAST, first_seen_at DESC""",
                (list(statuses),),
            ).fetchall()
            if not companies:
                return []
            ids = [c["id"] for c in companies]
            sigs = conn.execute(
                """SELECT company_id, source, signal_type, summary,
                          signal_strength, observed_at, payload
                     FROM discovery_signals WHERE company_id = ANY(%s)
                    ORDER BY observed_at DESC""",
                (ids,),
            ).fetchall()
        by_company: dict[int, list[dict]] = {}
        for s in sigs:
            by_company.setdefault(s["company_id"], []).append(s)
        return [_to_row(c, by_company.get(c["id"], [])) for c in companies]

    def stats(self) -> dict[str, int]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT icp_status, COUNT(*) AS n FROM discovery_companies "
                "GROUP BY icp_status"
            ).fetchall()
        counts = {r["icp_status"]: r["n"] for r in rows}
        counts["total"] = sum(counts.values())
        return counts

    def delete(self, keys: list[str] | None) -> int:
        """Delete companies by normalized_name; signals cascade (FK ON DELETE
        CASCADE). `keys=None` truncates the whole discovery store."""
        with self._pool.connection() as conn:
            if keys is None:
                cur = conn.execute("DELETE FROM discovery_companies")
            else:
                if not keys:
                    return 0
                cur = conn.execute(
                    "DELETE FROM discovery_companies WHERE normalized_name = ANY(%s)",
                    (list(keys),),
                )
            return cur.rowcount or 0

    # ── run heartbeat (powers the live "processing" marker) ──────────────

    def start_run(self, source: str) -> int:
        with self._pool.connection() as conn:
            row = conn.execute(
                "INSERT INTO connector_runs (source, status) "
                "VALUES (%s, 'running') RETURNING id",
                (source,),
            ).fetchone()
        return row["id"]

    def update_run(self, run_id: int, **counts: int) -> None:
        """Set any of planned/rows_fetched/new_companies/signals_added/
        companies_qualified on the running row (absolute values)."""
        cols = ("planned", "rows_fetched", "new_companies", "signals_added",
                "companies_qualified")
        sets = {c: counts[c] for c in cols if counts.get(c) is not None}
        if not sets:
            return
        assignments = ", ".join(f"{c} = %({c})s" for c in sets)
        with self._pool.connection() as conn:
            conn.execute(
                f"UPDATE connector_runs SET {assignments} WHERE id = %(id)s",
                {**sets, "id": run_id},
            )

    def finish_run(
        self, run_id: int, *, status: str = "success", error: str | None = None,
    ) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE connector_runs SET status = %s, error_message = %s, "
                "finished_at = now() WHERE id = %s",
                (status, error, run_id),
            )

    def active_runs(self, *, max_age_minutes: int = 15) -> list[dict]:
        """Runs still 'running' and started recently — a crashed run's row is
        ignored after max_age_minutes so the marker can't get stuck on."""
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT source, started_at, planned, rows_fetched, new_companies,
                          signals_added, companies_qualified,
                          EXTRACT(EPOCH FROM (now() - started_at))::int AS elapsed_seconds
                     FROM connector_runs
                    WHERE status = 'running'
                      AND started_at > now() - make_interval(mins => %s)
                    ORDER BY started_at DESC""",
                (max_age_minutes,),
            ).fetchall()
        for r in rows:
            r["started_at"] = _iso(r["started_at"])
        return rows

    def cleanup_stale_runs(self) -> int:
        """Fail any rows left 'running' (no live process can own them at boot)."""
        with self._pool.connection() as conn:
            cur = conn.execute(
                "UPDATE connector_runs SET status = 'failed', "
                "error_message = 'orphaned by restart', finished_at = now() "
                "WHERE status = 'running'")
            return cur.rowcount or 0

    # ── ABM target list ────────────────────────────────────────────────

    def replace_abm_targets(self, targets: list[dict]) -> int:
        """Replace the stored ABM target list wholesale; return rows stored."""
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("DELETE FROM abm_targets")
            for t in targets:
                conn.execute(
                    """INSERT INTO abm_targets
                         (name, aliases, keys, domain, state, segment,
                          source_sheet, definitive_id)
                       VALUES (%(name)s, %(aliases)s, %(keys)s, %(domain)s,
                               %(state)s, %(segment)s, %(source_sheet)s,
                               %(definitive_id)s)""",
                    {
                        "name": t.get("name"),
                        "aliases": Json(t.get("aliases") or []),
                        "keys": Json(t.get("keys") or []),
                        "domain": t.get("domain"),
                        "state": t.get("state"),
                        "segment": t.get("segment"),
                        "source_sheet": t.get("source_sheet"),
                        "definitive_id": t.get("definitive_id"),
                    },
                )
        return len(targets)

    def abm_targets(self) -> list[dict]:
        """All stored ABM target rows (for building the match index)."""
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT name, aliases, keys, domain, state, segment, "
                "source_sheet, definitive_id FROM abm_targets").fetchall()
        return [dict(r) for r in rows]

    def abm_targets_summary(self) -> dict:
        """Counts for the UI: total, by segment, last upload time."""
        with self._pool.connection() as conn:
            tot = conn.execute(
                "SELECT count(*) AS n, max(uploaded_at) AS up FROM abm_targets"
            ).fetchone()
            segs = conn.execute(
                "SELECT coalesce(segment, source_sheet, 'Other') AS seg, "
                "count(*) AS n FROM abm_targets GROUP BY 1 ORDER BY 2 DESC"
            ).fetchall()
        return {
            "total": tot["n"] if tot else 0,
            "uploaded_at": _iso(tot["up"]) if tot and tot["up"] else None,
            "by_segment": {r["seg"]: r["n"] for r in segs},
        }

    # ── monitored social accounts ────────────────────────────────────

    def social_targets(self) -> list[dict]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT linkedin_url, label, kind, active, created_at "
                "FROM social_targets ORDER BY kind, created_at"
            ).fetchall()
        return [{"linkedin_url": r["linkedin_url"], "label": r["label"],
                 "kind": r["kind"], "active": r["active"],
                 "created_at": _iso(r["created_at"])} for r in rows]

    def upsert_social_target(self, target: dict) -> dict:
        key = normalize_linkedin_url(target.get("linkedin_url"))
        with self._pool.connection() as conn:
            row = conn.execute(
                """INSERT INTO social_targets (url_key, linkedin_url, label, kind, active)
                   VALUES (%(k)s, %(url)s, %(label)s, %(kind)s, %(active)s)
                   ON CONFLICT (url_key) DO UPDATE SET
                       label = COALESCE(EXCLUDED.label, social_targets.label),
                       kind = EXCLUDED.kind, active = EXCLUDED.active
                   RETURNING linkedin_url, label, kind, active, created_at""",
                {"k": key, "url": target["linkedin_url"], "label": target.get("label"),
                 "kind": target.get("kind", "competitor"),
                 "active": target.get("active", True)},
            ).fetchone()
        return {"linkedin_url": row["linkedin_url"], "label": row["label"],
                "kind": row["kind"], "active": row["active"],
                "created_at": _iso(row["created_at"])}

    def delete_social_target(self, linkedin_url: str) -> bool:
        key = normalize_linkedin_url(linkedin_url)
        with self._pool.connection() as conn:
            cur = conn.execute("DELETE FROM social_targets WHERE url_key = %s", (key,))
            return cur.rowcount > 0

    def event_keywords(self) -> list[dict]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT keyword, label, active, created_at "
                "FROM event_keywords ORDER BY created_at"
            ).fetchall()
        return [{"keyword": r["keyword"], "label": r["label"], "active": r["active"],
                 "created_at": _iso(r["created_at"])} for r in rows]

    def upsert_event_keyword(self, keyword: dict) -> dict:
        key = _keyword_key(keyword.get("keyword"))
        with self._pool.connection() as conn:
            row = conn.execute(
                """INSERT INTO event_keywords (kw_key, keyword, label, active)
                   VALUES (%(k)s, %(kw)s, %(label)s, %(active)s)
                   ON CONFLICT (kw_key) DO UPDATE SET
                       label = COALESCE(EXCLUDED.label, event_keywords.label),
                       active = EXCLUDED.active
                   RETURNING keyword, label, active, created_at""",
                {"k": key, "kw": keyword["keyword"].strip(), "label": keyword.get("label"),
                 "active": keyword.get("active", True)},
            ).fetchone()
        return {"keyword": row["keyword"], "label": row["label"], "active": row["active"],
                "created_at": _iso(row["created_at"])}

    def delete_event_keyword(self, keyword: str) -> bool:
        with self._pool.connection() as conn:
            cur = conn.execute("DELETE FROM event_keywords WHERE kw_key = %s",
                               (_keyword_key(keyword),))
            return cur.rowcount > 0

    def recent_decisions(self, *, limit: int = 20) -> list[dict]:
        """Most recently decided companies (any verdict) — drives the run log.

        Includes disqualified so the feed shows every evaluation, not just wins.
        Carries the top signal so the UI can show what triggered the company."""
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT dc.normalized_name, dc.display_name, dc.icp_status,
                          dc.segment, dc.qualified_at,
                          ds.signal_type, ds.summary AS signal_summary
                     FROM discovery_companies dc
                LEFT JOIN LATERAL (
                    SELECT signal_type, summary
                      FROM discovery_signals
                     WHERE company_id = dc.id
                     ORDER BY observed_at DESC
                     LIMIT 1
                ) ds ON true
                    WHERE dc.qualified_at IS NOT NULL
                    ORDER BY dc.qualified_at DESC
                    LIMIT %s""",
                (limit,),
            ).fetchall()
        return [
            {"company_key": r["normalized_name"], "name": r["display_name"],
             "status": r["icp_status"], "segment": r.get("segment"),
             "at": _iso(r["qualified_at"]),
             "signal_type": r.get("signal_type"),
             "signal_summary": r.get("signal_summary")}
            for r in rows
        ]


# ── row mapping (match JsonFileRepository's dict shape exactly) ────────


def _to_row(company: dict, signals: list[dict]) -> dict:
    """Shape a DB row + its signals like the JSON repo's stored dict, so the
    ReviewService maps both identically.
    """
    return {
        "normalized_name": company["normalized_name"],
        "display_name": company["display_name"],
        "domain": company.get("domain"),
        "icp_status": company.get("icp_status"),
        "segment": company.get("segment"),
        "sub_segment": company.get("sub_segment"),
        "company_type": company.get("company_type"),
        "approximate_employees": company.get("approximate_employees"),
        "confidence": float(company["confidence"]) if company.get("confidence") is not None else None,
        "reasoning": company.get("reasoning"),
        "evidence_url": company.get("evidence_url"),
        "review_status": company.get("review_status", "pending"),
        "first_seen_at": _iso(company.get("first_seen_at")),
        "qualified_at": _iso(company.get("qualified_at")),
        "signals": [
            {
                "source": s["source"],
                "signal_type": s["signal_type"],
                "summary": s.get("summary"),
                "signal_strength": float(s["signal_strength"]) if s.get("signal_strength") is not None else None,
                "observed_at": _iso(s.get("observed_at")),
                "payload": s.get("payload") or {},
            }
            for s in signals
        ],
    }


def _iso(dt) -> str | None:
    return dt.isoformat() if isinstance(dt, datetime) else dt
