"""Storage for campaign automation (Phase 3) — interface + Postgres and JSON impls.

Own module, own tables (campaign_schema.sql), zero foreign keys into the live
discovery/scoring/engagement stores — `account_id` is the same soft text
reference the engagement store uses. Contacts are NOT stored here (they live in
Reply.io, mirrored by the engagement store); this records only decisions:

  • campaign_sequences   — ICP sequence key -> Reply.io campaign id (runtime data:
    Galyna assigns ids in the Campaigns tab once she builds each sequence in
    the Reply.io UI — the API cannot author sequences).
  • campaign_enrollments — the ledger: one row per (account, contact, campaign)
    push (enrolled / skipped_409 / failed). Dry-runs are never persisted.

`get_campaign_repository()` returns Postgres when DATABASE_URL is set, else a
JSON-file repo — mirroring get_repository() / get_engagement_repository().
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

# Terminal statuses that mean "this contact is handled for this campaign" — a
# failed row is NOT terminal, so a later run may retry it.
DONE_STATUSES = frozenset({"enrolled", "skipped_409"})


class CampaignRepository(Protocol):
    """Storage contract for campaign automation. Idempotent: recording the same
    (account, contact, campaign) twice keeps one row (latest status wins)."""

    def ensure_schema(self) -> None: ...

    def upsert_sequence(self, sequence_key: str, *, campaign_id: str | None,
                        campaign_name: str | None = None) -> None:
        """Assign (or clear, with None/empty) the Reply.io campaign for a key."""
        ...

    def sequences(self) -> list[dict]:
        """Every stored mapping row: {sequence_key, campaign_id, campaign_name}."""
        ...

    def upsert_channel_sequence(self, sequence_key: str, channel: str, *,
                                campaign_id: str | None,
                                campaign_name: str | None = None) -> None:
        """Per-channel mapping (linkedin/sms): assign the executor tool's
        campaign id for one (sequence key, channel)."""
        ...

    def channel_sequences(self) -> list[dict]:
        """Every stored per-channel mapping row."""
        ...

    def add_stop(self, row: dict) -> bool:
        """Record one stop-rule action (account, stopped channel, reason).
        Returns True only when NEWLY recorded — the sweep's idempotency claim."""
        ...

    def stops(self, *, account_id: str | None = None) -> list[dict]:
        """Stop-rule actions taken, newest first."""
        ...

    def add_enrollment(self, row: dict) -> bool:
        """Upsert one ledger row by (account_id, contact_ext, campaign_id).
        Returns True if newly inserted, False if it updated an existing row."""
        ...

    def enrollments(self, *, account_id: str | None = None,
                    limit: int = 500) -> list[dict]:
        """Ledger rows, newest first (optionally one account's)."""
        ...

    def enrolled_for(self, account_id: str, campaign_id: str) -> set[str]:
        """contact_exts already DONE (enrolled/409) for this account+campaign —
        the runner's contact-level dedup set."""
        ...

    def accounts_enrolled(self) -> set[str]:
        """account_ids with at least one DONE row — the board's 'Enrolled' badge
        and the eligibility exclusion set."""
        ...

    def delete_all(self) -> int:
        """Wipe the ledger + mapping (tests / clean replay)."""
        ...


# ── shared shaping ────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _enrollment_row(r: dict) -> dict:
    """Normalize an inbound ledger dict to the stored shape (defaults + types)."""
    status = r.get("status")
    if status not in ("enrolled", "skipped_409", "failed"):
        raise ValueError(f"bad enrollment status: {status!r}")
    return {
        "account_id": str(r["account_id"]),
        "account_name": r.get("account_name"),
        "contact_ext": str(r["contact_ext"]),
        "email": r.get("email"),
        "channel": r.get("channel") or "email",
        "sequence_key": str(r["sequence_key"]),
        "campaign_id": str(r["campaign_id"]),
        "status": status,
        "detail": r.get("detail") or {},
        "trigger": r.get("trigger") or "auto",
        "enrolled_at": r.get("enrolled_at") or _now(),
    }


def _norm(row: dict) -> dict:
    """ISO-stringify datetimes so Postgres reads match the JSON repo's shape
    (the dual-repo parity rule)."""
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in row.items()}


# ── JSON file (local / dev / tests) ───────────────────────────────────


class CampaignJsonRepository:
    """Reference implementation backed by a single JSON file. Mirrors the SQL
    tables 1:1. Not for production concurrency."""

    def __init__(self, path: str | Path = "./data/campaign_store.json") -> None:
        self._path = Path(path)
        self._store = self._load()

    def ensure_schema(self) -> None:
        return None

    def upsert_sequence(self, sequence_key, *, campaign_id, campaign_name=None) -> None:
        self._store["sequences"][sequence_key] = {
            "sequence_key": sequence_key,
            "campaign_id": (str(campaign_id) if campaign_id else None),
            "campaign_name": campaign_name,
            "updated_at": _now(),
        }
        self._flush()

    def sequences(self) -> list[dict]:
        return list(self._store["sequences"].values())

    def upsert_channel_sequence(self, sequence_key, channel, *, campaign_id,
                                campaign_name=None) -> None:
        if channel not in ("linkedin", "sms"):
            raise ValueError(f"bad channel: {channel!r}")
        self._store["channel_sequences"][f"{sequence_key}|{channel}"] = {
            "sequence_key": sequence_key, "channel": channel,
            "campaign_id": (str(campaign_id) if campaign_id else None),
            "campaign_name": campaign_name, "updated_at": _now(),
        }
        self._flush()

    def channel_sequences(self) -> list[dict]:
        return list(self._store["channel_sequences"].values())

    def add_stop(self, row) -> bool:
        key = f"{row['account_id']}|{row['channel']}|{row['reason']}"
        if key in self._store["stops"]:
            return False
        self._store["stops"][key] = {
            "account_id": row["account_id"], "channel": row["channel"],
            "reason": row["reason"], "detail": row.get("detail") or {},
            "stopped_at": row.get("stopped_at") or _now(),
        }
        self._flush()
        return True

    def stops(self, *, account_id=None) -> list[dict]:
        rows = list(self._store["stops"].values())
        if account_id is not None:
            rows = [r for r in rows if r.get("account_id") == account_id]
        rows.sort(key=lambda r: r.get("stopped_at") or "", reverse=True)
        return rows

    def add_enrollment(self, row) -> bool:
        r = _enrollment_row(row)
        key = f"{r['account_id']}|{r['contact_ext']}|{r['campaign_id']}"
        is_new = key not in self._store["enrollments"]
        self._store["enrollments"][key] = r
        self._flush()
        return is_new

    def enrollments(self, *, account_id=None, limit=500) -> list[dict]:
        rows = list(self._store["enrollments"].values())
        if account_id is not None:
            rows = [r for r in rows if r.get("account_id") == account_id]
        rows.sort(key=lambda r: r.get("enrolled_at") or "", reverse=True)
        return rows[:limit]

    def enrolled_for(self, account_id, campaign_id) -> set[str]:
        return {r["contact_ext"] for r in self._store["enrollments"].values()
                if r.get("account_id") == account_id
                and r.get("campaign_id") == str(campaign_id)
                and r.get("status") in DONE_STATUSES}

    def accounts_enrolled(self) -> set[str]:
        return {r["account_id"] for r in self._store["enrollments"].values()
                if r.get("status") in DONE_STATUSES}

    def delete_all(self) -> int:
        n = len(self._store["enrollments"])
        self._store = _empty_store()
        self._flush()
        return n

    # -- internals --

    def _load(self) -> dict:
        if not self._path.exists():
            return _empty_store()
        try:
            data = json.loads(self._path.read_text())
        except json.JSONDecodeError:
            backup = self._path.with_suffix(self._path.suffix + ".corrupt")
            try:
                self._path.replace(backup)
                logger.error("corrupt campaign store at %s — moved to %s, starting empty",
                             self._path, backup)
            except OSError:
                logger.error("corrupt campaign store at %s — starting empty", self._path)
            return _empty_store()
        for k, v in _empty_store().items():
            data.setdefault(k, v)
        return data

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._store, indent=2, default=str))
        tmp.replace(self._path)


def _empty_store() -> dict:
    return {"sequences": {}, "enrollments": {}, "channel_sequences": {}, "stops": {}}


# ── Postgres ──────────────────────────────────────────────────────────


class CampaignPostgresRepository:
    """Production storage backed by Postgres (psycopg3, pooled, sync) — same
    protocol as the JSON impl. Runs campaign_schema.sql on ensure_schema()."""

    def __init__(self, dsn: str | None = None) -> None:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        dsn = dsn or os.getenv("DATABASE_URL")
        if not dsn:
            raise RuntimeError("DATABASE_URL not set")
        self._pool = ConnectionPool(dsn, min_size=1, max_size=2, open=True,
                                    kwargs={"row_factory": dict_row})

    def close(self) -> None:
        self._pool.close()

    def ensure_schema(self) -> None:
        sql = (Path(__file__).resolve().parent / "campaign_schema.sql").read_text()
        with self._pool.connection() as conn:
            conn.execute(sql)
        logger.info("campaign schema ensured")

    def upsert_sequence(self, sequence_key, *, campaign_id, campaign_name=None) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                """INSERT INTO campaign_sequences (sequence_key, campaign_id, campaign_name, updated_at)
                   VALUES (%s, %s, %s, now())
                   ON CONFLICT (sequence_key) DO UPDATE SET
                     campaign_id = EXCLUDED.campaign_id,
                     campaign_name = EXCLUDED.campaign_name,
                     updated_at = now()""",
                (sequence_key, (str(campaign_id) if campaign_id else None), campaign_name),
            )

    def sequences(self) -> list[dict]:
        with self._pool.connection() as conn:
            rows = conn.execute("SELECT * FROM campaign_sequences").fetchall()
        return [_norm(dict(r)) for r in rows]

    def upsert_channel_sequence(self, sequence_key, channel, *, campaign_id,
                                campaign_name=None) -> None:
        if channel not in ("linkedin", "sms"):
            raise ValueError(f"bad channel: {channel!r}")
        with self._pool.connection() as conn:
            conn.execute(
                """INSERT INTO campaign_channel_sequences
                     (sequence_key, channel, campaign_id, campaign_name, updated_at)
                   VALUES (%s, %s, %s, %s, now())
                   ON CONFLICT (sequence_key, channel) DO UPDATE SET
                     campaign_id = EXCLUDED.campaign_id,
                     campaign_name = EXCLUDED.campaign_name,
                     updated_at = now()""",
                (sequence_key, channel,
                 (str(campaign_id) if campaign_id else None), campaign_name),
            )

    def channel_sequences(self) -> list[dict]:
        with self._pool.connection() as conn:
            rows = conn.execute("SELECT * FROM campaign_channel_sequences").fetchall()
        return [_norm(dict(r)) for r in rows]

    def add_stop(self, row) -> bool:
        from psycopg.types.json import Json
        with self._pool.connection() as conn:
            out = conn.execute(
                """INSERT INTO campaign_stops (account_id, channel, reason, detail)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (account_id, channel, reason) DO NOTHING
                   RETURNING id""",
                (row["account_id"], row["channel"], row["reason"],
                 Json(row.get("detail") or {})),
            ).fetchone()
        return out is not None

    def stops(self, *, account_id=None) -> list[dict]:
        with self._pool.connection() as conn:
            if account_id is not None:
                rows = conn.execute(
                    "SELECT * FROM campaign_stops WHERE account_id = %s "
                    "ORDER BY stopped_at DESC", (account_id,)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM campaign_stops ORDER BY stopped_at DESC").fetchall()
        return [_norm(dict(r)) for r in rows]

    def add_enrollment(self, row) -> bool:
        from psycopg.types.json import Json
        r = _enrollment_row(row)
        with self._pool.connection() as conn:
            out = conn.execute(
                """
                INSERT INTO campaign_enrollments (
                    account_id, account_name, contact_ext, email, channel,
                    sequence_key, campaign_id, status, detail, trigger, enrolled_at
                ) VALUES (
                    %(account_id)s, %(account_name)s, %(contact_ext)s, %(email)s,
                    %(channel)s, %(sequence_key)s, %(campaign_id)s, %(status)s,
                    %(detail)s, %(trigger)s, %(enrolled_at)s
                )
                ON CONFLICT (account_id, contact_ext, campaign_id) DO UPDATE SET
                    status = EXCLUDED.status, detail = EXCLUDED.detail,
                    trigger = EXCLUDED.trigger, enrolled_at = EXCLUDED.enrolled_at,
                    account_name = EXCLUDED.account_name, email = EXCLUDED.email
                RETURNING (xmax = 0) AS inserted
                """,
                {**r, "detail": Json(r["detail"])},
            ).fetchone()
        return bool(out["inserted"])

    def enrollments(self, *, account_id=None, limit=500) -> list[dict]:
        with self._pool.connection() as conn:
            if account_id is not None:
                rows = conn.execute(
                    "SELECT * FROM campaign_enrollments WHERE account_id = %s "
                    "ORDER BY enrolled_at DESC LIMIT %s", (account_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM campaign_enrollments "
                    "ORDER BY enrolled_at DESC LIMIT %s", (limit,),
                ).fetchall()
        return [_norm(dict(r)) for r in rows]

    def enrolled_for(self, account_id, campaign_id) -> set[str]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT contact_ext FROM campaign_enrollments "
                "WHERE account_id = %s AND campaign_id = %s "
                "AND status IN ('enrolled','skipped_409')",
                (account_id, str(campaign_id)),
            ).fetchall()
        return {r["contact_ext"] for r in rows}

    def accounts_enrolled(self) -> set[str]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT account_id FROM campaign_enrollments "
                "WHERE status IN ('enrolled','skipped_409')"
            ).fetchall()
        return {r["account_id"] for r in rows}

    def delete_all(self) -> int:
        with self._pool.connection() as conn:
            n = conn.execute("DELETE FROM campaign_enrollments").rowcount or 0
            conn.execute("DELETE FROM campaign_sequences")
            conn.execute("DELETE FROM campaign_channel_sequences")
            conn.execute("DELETE FROM campaign_stops")
        return n


# ── factory ───────────────────────────────────────────────────────────


def get_campaign_repository() -> CampaignRepository:
    """Postgres when DATABASE_URL is set; otherwise the JSON-file repo. Fails
    closed in production so real enrollment decisions never land in a file."""
    if os.getenv("DATABASE_URL"):
        return CampaignPostgresRepository()
    from auto_search.runtime import is_production
    if is_production():
        raise RuntimeError(
            "DATABASE_URL is required in production — refusing to run the campaign "
            "store on a JSON file."
        )
    return CampaignJsonRepository()
