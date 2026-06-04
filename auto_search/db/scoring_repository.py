"""Storage for the scoring phase — interface + Postgres and JSON implementations.

Separate from the discovery store (repository.py) so the two phases stay
decoupled, but it talks to the same database. One denormalized row per account
(scored_accounts) carries the whole lifecycle, so the Scored dashboard reads it
in a single query.

`get_scoring_repository()` returns Postgres when DATABASE_URL is set, else a
JSON-file repo for local/dev — mirroring get_repository() for discovery.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from auto_search.scoring.models import Account, ScoreResult

logger = logging.getLogger(__name__)

# "Active" = actively being scored (a live Claude call, costing money) — this
# drives the dashboard shimmer + the activity poll. 'queued' is deliberately NOT
# here: imports park in 'queued' for free until the user scores them on demand,
# so a 1000-row import must not look like 1000 accounts mid-score.
_ACTIVE_STATES = ("scoring",)

# A score (plus QA) takes ~1 minute; even a heavily rate-limited run is bounded
# at a few minutes per call. An account sitting in 'scoring' far longer than that
# was orphaned (its background task died, usually a service restart) and would
# otherwise tick "scoring" forever, so it is swept back to the queue.
STALE_SCORING_SECONDS = 1800   # 30 minutes


# ── interface ─────────────────────────────────────────────────────────


class ScoringRepository(Protocol):
    def ensure_schema(self) -> None: ...
    def upsert_account(self, account: Account, *, state: str,
                       import_label: str | None = None) -> dict: ...
    def import_labels(self) -> list[dict]: ...
    def set_state(self, account_id: str, state: str, *, error: str | None = None) -> None: ...
    def set_phase(self, account_id: str, phase: str | None) -> None: ...
    def save_score(self, account_id: str, score: ScoreResult) -> dict | None: ...
    def set_dossier_state(self, account_id: str, state: str | None,
                          error: str | None = None) -> None: ...
    def save_dossier(self, account_id: str, dossier) -> dict | None: ...
    def get(self, account_id: str) -> dict | None: ...
    def list_accounts(self) -> list[dict]: ...
    def active(self) -> list[dict]: ...
    def queued(self) -> list[dict]: ...
    def exists(self, account_id: str) -> bool: ...
    def cost_summary(self) -> dict: ...
    def recover_orphaned_scoring(self, older_than_seconds: int = 0) -> int: ...
    def reset_to_queued(self) -> int: ...


# ── shared row shaping ────────────────────────────────────────────────


def _row(account: dict) -> dict:
    """Shape a stored account into the object the UI consumes (scoringData.js).

    Adds a resolved `tier` for convenience; scored fields are null until scored.
    For in-flight accounts it also reports the phase + when scoring started, so
    the UI can show live elapsed time and a progress estimate.
    """
    band, label = account.get("tier_band"), account.get("tier_label")
    state = account.get("state", "queued")
    in_flight = state == "scoring"          # parked 'queued' has no live clock
    started = account.get("updated_at") if in_flight else None
    return {
        "phase": account.get("phase") if in_flight else None,
        "scoring_started_at": _iso(started),
        "elapsed_seconds": _elapsed(started) if in_flight else None,
        "account_id": account["account_id"],
        "name": account["name"],
        "segment": account.get("segment"),
        "sub_segment": account.get("sub_segment"),
        "domain": account.get("domain"),
        "source": account.get("source"),
        "discovery_company_key": account.get("discovery_company_key"),
        "approximate_employees": account.get("approximate_employees"),
        "framework": account.get("framework"),
        "state": account.get("state", "queued"),
        "max_total": account.get("max_total"),
        "total": account.get("total"),
        "tier": {"band": band, "label": label} if band else None,
        "tier_band": band,
        "tier_label": label,
        "dimensions": account.get("dimensions") or [],
        "recommendation": account.get("recommendation"),
        "qa": account.get("qa"),
        "firmographics": account.get("firmographics") or {},
        "discovery_signals": account.get("discovery_signals") or [],
        "model": account.get("model"),
        "cost_usd": _as_float(account.get("cost_usd")),
        "import_label": account.get("import_label"),
        "dossier": account.get("dossier"),
        "dossier_state": account.get("dossier_state"),
        "dossier_cost": _as_float(account.get("dossier_cost")),
        "dossier_generated_at": _iso(account.get("dossier_generated_at")),
        "dossier_error": account.get("dossier_error"),
        "error": account.get("error_message"),
        "scored_at": _iso(account.get("scored_at")),
        "created_at": _iso(account.get("created_at")),
    }


def _score_fields(score: ScoreResult) -> dict:
    return {
        "total": score.total,
        "max_total": score.max_total,
        "tier_band": score.tier_band,
        "tier_label": score.tier_label,
        "dimensions": [d.model_dump() for d in score.dimensions],
        "recommendation": score.recommendation,
        "qa": score.qa.model_dump() if score.qa else None,
        "model": score.model,
        "cost_usd": score.cost_usd,
        "scored_at": score.scored_at or datetime.now(UTC).isoformat(),
    }


def _iso(dt) -> str | None:
    return dt.isoformat() if isinstance(dt, datetime) else dt


def _as_float(v) -> float:
    try:
        return round(float(v), 4) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _month_start_iso() -> str:
    now = datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def _elapsed(dt) -> int | None:
    """Whole seconds since `dt` (a datetime or ISO string), or None."""
    if isinstance(dt, datetime):
        base = dt
    elif isinstance(dt, str):
        try:
            base = datetime.fromisoformat(dt)
        except ValueError:
            return None
    else:
        return None
    if base.tzinfo is None:
        base = base.replace(tzinfo=UTC)
    return max(0, int((datetime.now(UTC) - base).total_seconds()))


# ── Postgres ──────────────────────────────────────────────────────────


class ScoringPostgresRepository:
    def __init__(self, dsn: str | None = None) -> None:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        dsn = dsn or os.getenv("DATABASE_URL")
        if not dsn:
            raise RuntimeError("DATABASE_URL not set")
        self._pool = ConnectionPool(dsn, min_size=1, max_size=4, open=True,
                                    kwargs={"row_factory": dict_row})

    def close(self) -> None:
        self._pool.close()

    def ensure_schema(self) -> None:
        sql = (Path(__file__).resolve().parent / "scoring_schema.sql").read_text()
        with self._pool.connection() as conn:
            conn.execute(sql)
        logger.info("scoring schema ensured")

    def upsert_account(self, account: Account, *, state: str,
                       import_label: str | None = None) -> dict:
        now = datetime.now(UTC)
        with self._pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO scored_accounts (
                    account_id, source, discovery_company_key, name, segment,
                    framework, domain, sub_segment, approximate_employees,
                    firmographics, discovery_signals, state, max_total,
                    import_label, created_at, updated_at
                ) VALUES (
                    %(id)s, %(source)s, %(dck)s, %(name)s, %(segment)s,
                    %(framework)s, %(domain)s, %(sub)s, %(emp)s,
                    %(firmo)s, %(signals)s, %(state)s, %(max_total)s,
                    %(label)s, %(now)s, %(now)s
                )
                ON CONFLICT (account_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    segment = EXCLUDED.segment,
                    framework = EXCLUDED.framework,
                    domain = COALESCE(EXCLUDED.domain, scored_accounts.domain),
                    firmographics = EXCLUDED.firmographics,
                    discovery_signals = EXCLUDED.discovery_signals,
                    state = EXCLUDED.state,
                    import_label = COALESCE(EXCLUDED.import_label, scored_accounts.import_label),
                    updated_at = EXCLUDED.updated_at
                """,
                {
                    "id": account.account_id, "source": account.source,
                    "dck": account.discovery_company_key, "name": account.name,
                    "segment": account.segment, "framework": account.framework,
                    "domain": account.domain, "sub": account.sub_segment,
                    "emp": account.approximate_employees,
                    "firmo": json.dumps(account.firmographics, default=str),
                    "signals": json.dumps(account.discovery_signals, default=str),
                    "state": state, "max_total": _max_total(account),
                    "label": import_label, "now": now,
                },
            )
        return self.get(account.account_id)

    def import_labels(self) -> list[dict]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT import_label AS label, COUNT(*) AS count, "
                "MAX(created_at) AS latest FROM scored_accounts "
                "WHERE import_label IS NOT NULL GROUP BY import_label "
                "ORDER BY MAX(created_at) DESC"
            ).fetchall()
        return [{"label": r["label"], "count": r["count"]} for r in rows]

    def set_state(self, account_id: str, state: str, *, error: str | None = None) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE scored_accounts SET state=%s, error_message=%s, "
                "updated_at=now() WHERE account_id=%s",
                (state, error, account_id),
            )

    def set_phase(self, account_id: str, phase: str | None) -> None:
        # Phase changes must NOT reset updated_at — elapsed is measured from the
        # start of scoring.
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE scored_accounts SET phase=%s WHERE account_id=%s",
                (phase, account_id),
            )

    def save_score(self, account_id: str, score: ScoreResult) -> dict | None:
        f = _score_fields(score)
        with self._pool.connection() as conn:
            row = conn.execute(
                """
                UPDATE scored_accounts SET
                    state='scored', error_message=NULL, phase=NULL,
                    total=%(total)s, tier_band=%(band)s, tier_label=%(label)s,
                    dimensions=%(dims)s, recommendation=%(rec)s, qa=%(qa)s,
                    model=%(model)s, cost_usd=%(cost)s,
                    scored_at=%(scored_at)s, updated_at=now()
                 WHERE account_id=%(id)s
             RETURNING account_id
                """,
                {
                    "id": account_id, "total": f["total"], "band": f["tier_band"],
                    "label": f["tier_label"], "dims": json.dumps(f["dimensions"]),
                    "rec": f["recommendation"],
                    "qa": json.dumps(f["qa"]) if f["qa"] is not None else None,
                    "model": f["model"], "cost": f["cost_usd"],
                    "scored_at": f["scored_at"],
                },
            ).fetchone()
        return self.get(account_id) if row else None

    def set_dossier_state(self, account_id: str, state: str | None,
                          error: str | None = None) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE scored_accounts SET dossier_state=%s, dossier_error=%s "
                "WHERE account_id=%s",
                (state, error, account_id),
            )

    def save_dossier(self, account_id: str, dossier) -> dict | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """
                UPDATE scored_accounts SET
                    dossier=%(d)s, dossier_state='ready', dossier_error=NULL,
                    dossier_cost=%(cost)s, dossier_generated_at=%(at)s
                 WHERE account_id=%(id)s
             RETURNING account_id
                """,
                {
                    "id": account_id,
                    "d": json.dumps(dossier.model_dump(), default=str),
                    "cost": dossier.cost_usd,
                    "at": dossier.generated_at or datetime.now(UTC).isoformat(),
                },
            ).fetchone()
        return self.get(account_id) if row else None

    def get(self, account_id: str) -> dict | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM scored_accounts WHERE account_id=%s", (account_id,)
            ).fetchone()
        return _row(row) if row else None

    def list_accounts(self) -> list[dict]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM scored_accounts ORDER BY updated_at DESC"
            ).fetchall()
        return [_row(r) for r in rows]

    def active(self) -> list[dict]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM scored_accounts WHERE state = ANY(%s) "
                "ORDER BY updated_at DESC",
                (list(_ACTIVE_STATES),),
            ).fetchall()
        return [_row(r) for r in rows]

    def queued(self) -> list[dict]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM scored_accounts WHERE state='queued' "
                "ORDER BY created_at ASC"
            ).fetchall()
        return [_row(r) for r in rows]

    def exists(self, account_id: str) -> bool:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM scored_accounts WHERE account_id=%s", (account_id,)
            ).fetchone()
        return row is not None

    def cost_summary(self) -> dict:
        with self._pool.connection() as conn:
            row = conn.execute(
                """
                SELECT
                  COALESCE(SUM(cost_usd + dossier_cost), 0) AS total_cost,
                  COALESCE(SUM(cost_usd) FILTER (
                      WHERE scored_at >= date_trunc('month', now())), 0)
                    + COALESCE(SUM(dossier_cost) FILTER (
                      WHERE dossier_generated_at >= date_trunc('month', now())), 0)
                    AS month_cost,
                  COUNT(*) FILTER (WHERE state='scored')  AS scored_count,
                  COUNT(*) FILTER (WHERE state='queued')  AS queued_count,
                  COALESCE(SUM(cost_usd) FILTER (WHERE source='csv' AND state='scored'), 0) AS csv_cost,
                  COUNT(*) FILTER (WHERE source='csv' AND state='scored') AS csv_count
                FROM scored_accounts
                """
            ).fetchone()
        csv_avg = (row["csv_cost"] / row["csv_count"]) if row["csv_count"] else 0.0
        return _cost_summary(row["total_cost"], row["month_cost"],
                             row["scored_count"], row["queued_count"], csv_avg)

    def recover_orphaned_scoring(self, older_than_seconds: int = 0) -> int:
        """Return stuck 'scoring' accounts to the queue. With the default 0 this
        resets every in-flight row (call at startup, when no task is alive); a
        positive threshold only sweeps rows stalled longer than that."""
        with self._pool.connection() as conn:
            cur = conn.execute(
                "UPDATE scored_accounts SET state='queued', phase=NULL, "
                "updated_at=now() WHERE state='scoring' "
                "AND updated_at <= now() - make_interval(secs => %s)",
                (older_than_seconds,),
            )
            return cur.rowcount or 0

    def reset_to_queued(self) -> int:
        """Clear every score back to a parked 'queued' account (non-destructive):
        the accounts stay, but their score, QA, tier and measured cost are wiped
        so they can be re-scored on demand. Used to re-measure cost from clean."""
        with self._pool.connection() as conn:
            cur = conn.execute(
                "UPDATE scored_accounts SET state='queued', phase=NULL, "
                "total=NULL, tier_band=NULL, tier_label=NULL, dimensions=NULL, "
                "recommendation=NULL, qa=NULL, cost_usd=0, scored_at=NULL, "
                "error_message=NULL, updated_at=now() WHERE state <> 'queued'"
            )
            return cur.rowcount or 0


# ── JSON file (local/dev) ─────────────────────────────────────────────


class ScoringJsonRepository:
    def __init__(self, path: str | Path = "./data/scoring_store.json") -> None:
        self._path = Path(path)
        self._store: dict[str, dict] = self._load()

    def ensure_schema(self) -> None:
        return None

    def upsert_account(self, account: Account, *, state: str,
                       import_label: str | None = None) -> dict:
        now = datetime.now(UTC).isoformat()
        existing = self._store.get(account.account_id, {})
        existing.update({
            "account_id": account.account_id, "source": account.source,
            "discovery_company_key": account.discovery_company_key,
            "name": account.name, "segment": account.segment,
            "framework": account.framework, "domain": account.domain,
            "sub_segment": account.sub_segment,
            "approximate_employees": account.approximate_employees,
            "firmographics": account.firmographics,
            "discovery_signals": account.discovery_signals,
            "state": state, "max_total": _max_total(account),
            "updated_at": now,
        })
        if import_label or not existing.get("import_label"):
            existing["import_label"] = import_label
        existing.setdefault("created_at", now)
        self._store[account.account_id] = existing
        self._flush()
        return _row(existing)

    def import_labels(self) -> list[dict]:
        agg: dict[str, dict] = {}
        for r in self._store.values():
            label = r.get("import_label")
            if not label:
                continue
            slot = agg.setdefault(label, {"label": label, "count": 0, "latest": ""})
            slot["count"] += 1
            slot["latest"] = max(slot["latest"], r.get("created_at") or "")
        rows = sorted(agg.values(), key=lambda s: s["latest"], reverse=True)
        return [{"label": s["label"], "count": s["count"]} for s in rows]

    def set_state(self, account_id: str, state: str, *, error: str | None = None) -> None:
        row = self._store.get(account_id)
        if row:
            row["state"] = state
            row["error_message"] = error
            row["updated_at"] = datetime.now(UTC).isoformat()
            self._flush()

    def set_phase(self, account_id: str, phase: str | None) -> None:
        row = self._store.get(account_id)
        if row:
            row["phase"] = phase           # no updated_at change — keep the clock
            self._flush()

    def save_score(self, account_id: str, score: ScoreResult) -> dict | None:
        row = self._store.get(account_id)
        if not row:
            return None
        row.update(_score_fields(score))
        row["state"] = "scored"
        row["error_message"] = None
        row["phase"] = None
        row["updated_at"] = datetime.now(UTC).isoformat()
        self._flush()
        return _row(row)

    def set_dossier_state(self, account_id: str, state: str | None,
                          error: str | None = None) -> None:
        row = self._store.get(account_id)
        if row:
            row["dossier_state"] = state
            row["dossier_error"] = error
            self._flush()

    def save_dossier(self, account_id: str, dossier) -> dict | None:
        row = self._store.get(account_id)
        if not row:
            return None
        row["dossier"] = dossier.model_dump()
        row["dossier_state"] = "ready"
        row["dossier_error"] = None
        row["dossier_cost"] = dossier.cost_usd
        row["dossier_generated_at"] = dossier.generated_at or datetime.now(UTC).isoformat()
        self._flush()
        return _row(row)

    def get(self, account_id: str) -> dict | None:
        row = self._store.get(account_id)
        return _row(row) if row else None

    def list_accounts(self) -> list[dict]:
        rows = sorted(self._store.values(),
                      key=lambda r: r.get("updated_at") or "", reverse=True)
        return [_row(r) for r in rows]

    def active(self) -> list[dict]:
        return [_row(r) for r in self._store.values()
                if r.get("state") in _ACTIVE_STATES]

    def queued(self) -> list[dict]:
        rows = [r for r in self._store.values() if r.get("state") == "queued"]
        rows.sort(key=lambda r: r.get("created_at") or "")
        return [_row(r) for r in rows]

    def exists(self, account_id: str) -> bool:
        return account_id in self._store

    def cost_summary(self) -> dict:
        month_start = _month_start_iso()
        total = month = csv_cost = 0.0
        scored = queued = csv_count = 0
        for r in self._store.values():
            state = r.get("state")
            if state == "scored":
                scored += 1
                if r.get("source") == "csv":
                    csv_cost += _as_float(r.get("cost_usd"))
                    csv_count += 1
            elif state == "queued":
                queued += 1
            c = _as_float(r.get("cost_usd"))
            total += c
            if (_iso(r.get("scored_at")) or "") >= month_start:
                month += c
            # dossier spend counts toward the budget too
            dc = _as_float(r.get("dossier_cost"))
            if dc:
                total += dc
                if (_iso(r.get("dossier_generated_at")) or "") >= month_start:
                    month += dc
        csv_avg = (csv_cost / csv_count) if csv_count else 0.0
        return _cost_summary(total, month, scored, queued, csv_avg)

    def recover_orphaned_scoring(self, older_than_seconds: int = 0) -> int:
        n = 0
        for row in self._store.values():
            if row.get("state") != "scoring":
                continue
            elapsed = _elapsed(row.get("updated_at"))
            if elapsed is None or elapsed >= older_than_seconds:
                row["state"] = "queued"
                row["phase"] = None
                row["updated_at"] = datetime.now(UTC).isoformat()
                n += 1
        if n:
            self._flush()
        return n

    def reset_to_queued(self) -> int:
        n = 0
        for row in self._store.values():
            if row.get("state") == "queued":
                continue
            row.update({
                "state": "queued", "phase": None, "total": None,
                "tier_band": None, "tier_label": None, "dimensions": None,
                "recommendation": None, "qa": None, "cost_usd": 0,
                "scored_at": None, "error_message": None,
                "updated_at": datetime.now(UTC).isoformat(),
            })
            n += 1
        if n:
            self._flush()
        return n

    # -- internals --

    def _load(self) -> dict[str, dict]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text())
        except json.JSONDecodeError:
            # Never silently wipe data: preserve the corrupt file before starting
            # fresh, so a bad write can't erase real scoring history.
            backup = self._path.with_suffix(self._path.suffix + ".corrupt")
            try:
                self._path.replace(backup)
                logger.error("corrupt scoring store at %s — moved to %s, starting empty",
                             self._path, backup)
            except OSError:
                logger.error("corrupt scoring store at %s — starting empty", self._path)
            return {}

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._store, indent=2, default=str))
        tmp.replace(self._path)


# ── helpers + factory ─────────────────────────────────────────────────


# The board-approved scoring budget. Surfaced with spend so the meter reads
# against a fixed number; override with SCORING_MONTHLY_BUDGET if it changes.
_MONTHLY_BUDGET = _as_float(os.getenv("SCORING_MONTHLY_BUDGET")) or 200.0


def _cost_summary(total, month, scored, queued, csv_avg=0.0) -> dict:
    total = _as_float(total)
    month = _as_float(month)
    scored = int(scored or 0)
    return {
        "total_cost": total,
        "month_cost": month,
        "monthly_budget": _MONTHLY_BUDGET,
        "budget_remaining": round(max(0.0, _MONTHLY_BUDGET - month), 2),
        "scored_count": scored,
        "queued_count": int(queued or 0),
        "avg_cost": round(total / scored, 4) if scored else 0.0,
        # measured average for CSV-source accounts only — the right basis for a
        # CSV import estimate (CSV scoring skips QA, so it is cheaper).
        "csv_avg_cost": round(_as_float(csv_avg), 4),
    }


def _max_total(account: Account) -> int:
    from auto_search.scoring.frameworks import FRAMEWORKS, framework_for_segment
    fw = FRAMEWORKS.get(account.framework) or framework_for_segment(account.segment)
    return fw.max_total


def get_scoring_repository() -> ScoringRepository:
    if os.getenv("DATABASE_URL"):
        return ScoringPostgresRepository()
    # Fail closed: real (production) data must not silently land in a JSON file.
    from auto_search.runtime import is_production
    if is_production():
        raise RuntimeError(
            "DATABASE_URL is required in production — refusing to run the scoring "
            "store on a JSON file where data would not persist or be backed up."
        )
    return ScoringJsonRepository()
