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

_ACTIVE_STATES = ("queued", "scoring")


# ── interface ─────────────────────────────────────────────────────────


class ScoringRepository(Protocol):
    def ensure_schema(self) -> None: ...
    def upsert_account(self, account: Account, *, state: str) -> dict: ...
    def set_state(self, account_id: str, state: str, *, error: str | None = None) -> None: ...
    def save_score(self, account_id: str, score: ScoreResult) -> dict | None: ...
    def get(self, account_id: str) -> dict | None: ...
    def list_accounts(self) -> list[dict]: ...
    def active(self) -> list[dict]: ...
    def exists(self, account_id: str) -> bool: ...


# ── shared row shaping ────────────────────────────────────────────────


def _row(account: dict) -> dict:
    """Shape a stored account into the object the UI consumes (scoringData.js).

    Adds a resolved `tier` for convenience; scored fields are null until scored.
    """
    band, label = account.get("tier_band"), account.get("tier_label")
    return {
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
        "scored_at": score.scored_at or datetime.now(UTC).isoformat(),
    }


def _iso(dt) -> str | None:
    return dt.isoformat() if isinstance(dt, datetime) else dt


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

    def upsert_account(self, account: Account, *, state: str) -> dict:
        now = datetime.now(UTC)
        with self._pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO scored_accounts (
                    account_id, source, discovery_company_key, name, segment,
                    framework, domain, sub_segment, approximate_employees,
                    firmographics, discovery_signals, state, max_total,
                    created_at, updated_at
                ) VALUES (
                    %(id)s, %(source)s, %(dck)s, %(name)s, %(segment)s,
                    %(framework)s, %(domain)s, %(sub)s, %(emp)s,
                    %(firmo)s, %(signals)s, %(state)s, %(max_total)s,
                    %(now)s, %(now)s
                )
                ON CONFLICT (account_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    segment = EXCLUDED.segment,
                    framework = EXCLUDED.framework,
                    domain = COALESCE(EXCLUDED.domain, scored_accounts.domain),
                    firmographics = EXCLUDED.firmographics,
                    discovery_signals = EXCLUDED.discovery_signals,
                    state = EXCLUDED.state,
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
                    "state": state, "max_total": _max_total(account), "now": now,
                },
            )
        return self.get(account.account_id)

    def set_state(self, account_id: str, state: str, *, error: str | None = None) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE scored_accounts SET state=%s, error_message=%s, "
                "updated_at=now() WHERE account_id=%s",
                (state, error, account_id),
            )

    def save_score(self, account_id: str, score: ScoreResult) -> dict | None:
        f = _score_fields(score)
        with self._pool.connection() as conn:
            row = conn.execute(
                """
                UPDATE scored_accounts SET
                    state='scored', error_message=NULL,
                    total=%(total)s, tier_band=%(band)s, tier_label=%(label)s,
                    dimensions=%(dims)s, recommendation=%(rec)s, qa=%(qa)s,
                    model=%(model)s, scored_at=%(scored_at)s, updated_at=now()
                 WHERE account_id=%(id)s
             RETURNING account_id
                """,
                {
                    "id": account_id, "total": f["total"], "band": f["tier_band"],
                    "label": f["tier_label"], "dims": json.dumps(f["dimensions"]),
                    "rec": f["recommendation"],
                    "qa": json.dumps(f["qa"]) if f["qa"] is not None else None,
                    "model": f["model"], "scored_at": f["scored_at"],
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

    def exists(self, account_id: str) -> bool:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM scored_accounts WHERE account_id=%s", (account_id,)
            ).fetchone()
        return row is not None


# ── JSON file (local/dev) ─────────────────────────────────────────────


class ScoringJsonRepository:
    def __init__(self, path: str | Path = "./data/scoring_store.json") -> None:
        self._path = Path(path)
        self._store: dict[str, dict] = self._load()

    def ensure_schema(self) -> None:
        return None

    def upsert_account(self, account: Account, *, state: str) -> dict:
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
        existing.setdefault("created_at", now)
        self._store[account.account_id] = existing
        self._flush()
        return _row(existing)

    def set_state(self, account_id: str, state: str, *, error: str | None = None) -> None:
        row = self._store.get(account_id)
        if row:
            row["state"] = state
            row["error_message"] = error
            row["updated_at"] = datetime.now(UTC).isoformat()
            self._flush()

    def save_score(self, account_id: str, score: ScoreResult) -> dict | None:
        row = self._store.get(account_id)
        if not row:
            return None
        row.update(_score_fields(score))
        row["state"] = "scored"
        row["error_message"] = None
        row["updated_at"] = datetime.now(UTC).isoformat()
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

    def exists(self, account_id: str) -> bool:
        return account_id in self._store

    # -- internals --

    def _load(self) -> dict[str, dict]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text())
        except json.JSONDecodeError:
            return {}

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._store, indent=2, default=str))
        tmp.replace(self._path)


# ── helpers + factory ─────────────────────────────────────────────────


def _max_total(account: Account) -> int:
    from auto_search.scoring.frameworks import FRAMEWORKS, framework_for_segment
    fw = FRAMEWORKS.get(account.framework) or framework_for_segment(account.segment)
    return fw.max_total


def get_scoring_repository() -> ScoringRepository:
    if os.getenv("DATABASE_URL"):
        return ScoringPostgresRepository()
    return ScoringJsonRepository()
