"""Storage for the engagement phase — interface + Postgres and JSON impls.

Engagement (Reply.io today; more sources later) is its own module. It lives in the
same database as the discovery store (repository.py) and the scoring store
(scoring_repository.py) but shares NO tables and holds NO foreign keys into them:
`account_id` is a SOFT reference (text) to a scored/ABM account, re-stamped on each
sync from durable keys (email domain / normalized company name), so it self-heals
if ids change and can never lock the live ABM/scoring tables.

`get_engagement_repository()` returns Postgres when DATABASE_URL is set, else a
JSON-file repo for local/dev + tests — mirroring get_repository() /
get_scoring_repository().

Two normalized tables + a derived rollup (see engagement_schema.sql):
  • engagement_contacts — one row per source contact: identity, the cross-match,
    and per-window send-stat COUNTS (for open/reply rates).
  • engagement_events   — one row per contact × MEANINGFUL touch (click / reply /
    meeting_booked). external_id is "<channel>:<kind>:<contactId>" (source is its own
    column, not repeated); the PK (source, external_id) makes re-sync idempotent and
    enforces "a contact counts each kind at most once", so a long contact list can't
    inflate an account's score.
  • engaged_accounts (view) — read-time rollup; the heat TIER is assigned by the
    pure Python scorer (engagement/scoring.py), never in SQL.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# Default source for this phase. Kept as a constant so a future connector can pass
# its own ('sfdc', 'sheet', ...) without any other code changing.
SOURCE_REPLYIO = "replyio"


# ── interface ─────────────────────────────────────────────────────────


class EngagementRepository(Protocol):
    """Storage contract for engagement. Implementations must be idempotent:
    upserting the same contact/event twice leaves the store as if done once."""

    def ensure_schema(self) -> None: ...

    def land_raw(self, kind: str, payload: dict, *, source: str = SOURCE_REPLYIO,
                 window_from: str | None = None, window_to: str | None = None) -> None:
        """Append a verbatim source payload to the raw landing table (ELT)."""
        ...

    def upsert_contact(self, contact: dict) -> None:
        """Insert/update one contact by (source, external_id). Carries identity,
        send-stat counts, and the cross-match (account_id/match_tier/matched_lists)."""
        ...

    def add_event(self, event: dict) -> bool:
        """Upsert one event by (source, external_id). Returns True if it was newly
        inserted, False if it updated an existing row — so callers can assert
        idempotency (re-sync must not create duplicates)."""
        ...

    def engaged_accounts(self) -> list[dict]:
        """The rollup: one row per matched account (score + counts + rates inputs),
        ranked by score then recency. Tier is applied by the caller (pure scorer)."""
        ...

    def events_for_account(self, account_id: str) -> list[dict]:
        """An account's meaningful touches, newest first (the drawer timeline)."""
        ...

    def contacts(self, *, account_id: str | None = None,
                 unresolved_only: bool = False) -> list[dict]:
        """Contacts, optionally filtered to one account or to the unresolved queue
        (account_id IS NULL — never dropped, surfaced for review)."""
        ...

    def recent_events(self, *, limit: int = 200) -> list[dict]:
        """Most recent meaningful touches across all accounts (the Inbox feed)."""
        ...

    def get_sync_state(self, source: str = SOURCE_REPLYIO) -> dict | None: ...

    def set_sync_state(self, source: str = SOURCE_REPLYIO, *, status: str | None = None,
                       stats: dict | None = None, error: str | None = None,
                       window_from: str | None = None, window_to: str | None = None,
                       last_synced_at: Any = None) -> None:
        """Upsert the per-source sync cursor. Provided fields overwrite; omitted
        ones (None) keep their prior value; `error` is always set (success clears
        it); `last_synced_at` defaults to now. Identical in both impls."""
        ...

    def delete_all(self) -> int:
        """Wipe every engagement row (clean slate for a full re-sync / tests)."""
        ...


# ── shared shaping ────────────────────────────────────────────────────


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _now() -> str:
    return datetime.now(UTC).isoformat()


_COUNT_FIELDS = ("sent", "delivered", "opened", "clicked", "replied", "bounced")


def _contact_row(c: dict) -> dict:
    """Normalize an inbound contact dict to the stored shape (defaults + types)."""
    return {
        "source": c.get("source") or SOURCE_REPLYIO,
        "external_id": str(c["external_id"]),
        "email": c.get("email"),
        "email_domain": c.get("email_domain"),
        "company": c.get("company"),
        "company_key": c.get("company_key"),
        "title": c.get("title"),
        "meeting_booked": bool(c.get("meeting_booked")),
        "opted_out": bool(c.get("opted_out")),
        **{f: int(c.get(f) or 0) for f in _COUNT_FIELDS},
        "account_id": c.get("account_id"),
        "match_tier": c.get("match_tier"),
        "matched_lists": list(c.get("matched_lists") or []),
        "updated_at": _now(),
    }


def _event_row(e: dict) -> dict:
    """Normalize an inbound event dict to the stored shape (defaults + types)."""
    return {
        "source": e.get("source") or SOURCE_REPLYIO,
        "external_id": str(e["external_id"]),
        "channel": e.get("channel") or "email",
        "kind": e["kind"],
        "points": int(e.get("points") or 0),
        "contact_ext": e.get("contact_ext"),
        "company": e.get("company"),
        "account_id": e.get("account_id"),
        "campaign": e.get("campaign"),
        "occurred_at": _iso(e["occurred_at"]),
        "raw": e.get("raw") or {},
    }


def _engaged_row(account_id: str, ev: dict, c: dict) -> dict:
    """Shape one engaged-account rollup row (matches the SQL view's columns)."""
    return {
        "account_id": account_id,
        "score": int(ev.get("score", 0)),
        "clicks": int(ev.get("clicks", 0)),
        "replies": int(ev.get("replies", 0)),
        "meetings": int(ev.get("meetings", 0)),
        "contacts": int(c.get("contacts", 0)),
        "delivered": c.get("delivered"),
        "opened": c.get("opened"),
        "replied_sends": c.get("replied_sends"),
        "last_touch": ev.get("last_touch"),
    }


# ── JSON file (local / dev / tests) ───────────────────────────────────


class EngagementJsonRepository:
    """Reference implementation backed by a single JSON file. Mirrors the on-disk
    shape of the SQL tables so the JSON→Postgres mapping stays 1:1. Not for
    production concurrency (rewrites the whole file per write)."""

    def __init__(self, path: str | Path = "./data/engagement_store.json") -> None:
        self._path = Path(path)
        self._store = self._load()

    def ensure_schema(self) -> None:
        return None

    def land_raw(self, kind, payload, *, source=SOURCE_REPLYIO,
                 window_from=None, window_to=None) -> None:
        self._store["raw"].append({
            "source": source, "kind": kind, "fetched_at": _now(),
            "window_from": window_from, "window_to": window_to, "payload": payload,
        })
        self._flush()

    def upsert_contact(self, contact) -> None:
        row = _contact_row(contact)
        self._store["contacts"][f"{row['source']}:{row['external_id']}"] = row
        self._flush()

    def add_event(self, event) -> bool:
        row = _event_row(event)
        key = f"{row['source']}:{row['external_id']}"
        is_new = key not in self._store["events"]
        existing = self._store["events"].get(key, {})
        row["ingested_at"] = existing.get("ingested_at") or _now()
        self._store["events"][key] = row
        self._flush()
        return is_new

    def engaged_accounts(self) -> list[dict]:
        ev: dict[str, dict] = {}
        for e in self._store["events"].values():
            aid = e.get("account_id")
            if not aid:
                continue
            slot = ev.setdefault(aid, {"score": 0, "clicks": 0, "replies": 0,
                                       "meetings": 0, "last_touch": None})
            slot["score"] += int(e.get("points") or 0)
            kind = e.get("kind")
            if kind == "click":
                slot["clicks"] += 1
            elif kind == "reply":
                slot["replies"] += 1
            elif kind == "meeting_booked":
                slot["meetings"] += 1
            ot = e.get("occurred_at")
            if ot and (slot["last_touch"] is None or ot > slot["last_touch"]):
                slot["last_touch"] = ot
        cc: dict[str, dict] = {}
        for c in self._store["contacts"].values():
            aid = c.get("account_id")
            if not aid:
                continue
            slot = cc.setdefault(aid, {"contacts": 0, "delivered": 0,
                                       "opened": 0, "replied_sends": 0})
            slot["contacts"] += 1
            slot["delivered"] += int(c.get("delivered") or 0)
            slot["opened"] += int(c.get("opened") or 0)
            slot["replied_sends"] += int(c.get("replied") or 0)
        rows = [_engaged_row(aid, ev.get(aid, {}), cc.get(aid, {}))
                for aid in (set(ev) | set(cc))]
        rows.sort(key=lambda r: (r["score"], r.get("last_touch") or ""), reverse=True)
        return rows

    def events_for_account(self, account_id) -> list[dict]:
        rows = [e for e in self._store["events"].values()
                if e.get("account_id") == account_id]
        rows.sort(key=lambda e: e.get("occurred_at") or "", reverse=True)
        return rows

    def contacts(self, *, account_id=None, unresolved_only=False) -> list[dict]:
        rows = list(self._store["contacts"].values())
        if unresolved_only:
            rows = [c for c in rows if not c.get("account_id")]
        elif account_id is not None:
            rows = [c for c in rows if c.get("account_id") == account_id]
        rows.sort(key=lambda c: c.get("updated_at") or "", reverse=True)
        return rows

    def recent_events(self, *, limit=200) -> list[dict]:
        rows = sorted(self._store["events"].values(),
                      key=lambda e: e.get("occurred_at") or "", reverse=True)
        return rows[:limit]

    def get_sync_state(self, source=SOURCE_REPLYIO) -> dict | None:
        return self._store["sync"].get(source)

    def set_sync_state(self, source=SOURCE_REPLYIO, *, status=None, stats=None, error=None,
                       window_from=None, window_to=None, last_synced_at=None) -> None:
        row = self._store["sync"].get(source) or {"source": source}
        if status is not None:
            row["status"] = status
        if stats is not None:
            row["stats"] = stats
        if window_from is not None:
            row["window_from"] = window_from
        if window_to is not None:
            row["window_to"] = window_to
        row["error"] = error                       # always set: a success clears it
        row["last_synced_at"] = _iso(last_synced_at) or _now()
        self._store["sync"][source] = row
        self._flush()

    def delete_all(self) -> int:
        n = len(self._store["events"]) + len(self._store["contacts"])
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
                logger.error("corrupt engagement store at %s — moved to %s, starting empty",
                             self._path, backup)
            except OSError:
                logger.error("corrupt engagement store at %s — starting empty", self._path)
            return _empty_store()
        # tolerate older/partial files
        for k, v in _empty_store().items():
            data.setdefault(k, v)
        return data

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._store, indent=2, default=str))
        tmp.replace(self._path)


def _empty_store() -> dict:
    return {"raw": [], "contacts": {}, "events": {}, "sync": {}}


# ── Postgres ──────────────────────────────────────────────────────────


class EngagementPostgresRepository:
    """Production storage backed by Postgres (psycopg3, pooled, sync) — same
    protocol as the JSON impl. Runs engagement_schema.sql on ensure_schema()."""

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
        sql = (Path(__file__).resolve().parent / "engagement_schema.sql").read_text()
        with self._pool.connection() as conn:
            conn.execute(sql)
        logger.info("engagement schema ensured")

    def land_raw(self, kind, payload, *, source=SOURCE_REPLYIO,
                 window_from=None, window_to=None) -> None:
        from psycopg.types.json import Json
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO engagement_raw (source, kind, window_from, window_to, payload) "
                "VALUES (%s, %s, %s, %s, %s)",
                (source, kind, window_from, window_to, Json(payload)),
            )

    def upsert_contact(self, contact) -> None:
        from psycopg.types.json import Json
        r = _contact_row(contact)
        with self._pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO engagement_contacts (
                    source, external_id, email, email_domain, company, company_key,
                    title, meeting_booked, opted_out, sent, delivered, opened,
                    clicked, replied, bounced, account_id, match_tier, matched_lists,
                    updated_at
                ) VALUES (
                    %(source)s, %(external_id)s, %(email)s, %(email_domain)s, %(company)s,
                    %(company_key)s, %(title)s, %(meeting_booked)s, %(opted_out)s, %(sent)s,
                    %(delivered)s, %(opened)s, %(clicked)s, %(replied)s, %(bounced)s,
                    %(account_id)s, %(match_tier)s, %(matched_lists)s, now()
                )
                ON CONFLICT (source, external_id) DO UPDATE SET
                    email = EXCLUDED.email, email_domain = EXCLUDED.email_domain,
                    company = EXCLUDED.company, company_key = EXCLUDED.company_key,
                    title = EXCLUDED.title, meeting_booked = EXCLUDED.meeting_booked,
                    opted_out = EXCLUDED.opted_out, sent = EXCLUDED.sent,
                    delivered = EXCLUDED.delivered, opened = EXCLUDED.opened,
                    clicked = EXCLUDED.clicked, replied = EXCLUDED.replied,
                    bounced = EXCLUDED.bounced, account_id = EXCLUDED.account_id,
                    match_tier = EXCLUDED.match_tier, matched_lists = EXCLUDED.matched_lists,
                    updated_at = now()
                """,
                {**r, "matched_lists": Json(r["matched_lists"])},
            )

    def add_event(self, event) -> bool:
        from psycopg.types.json import Json
        r = _event_row(event)
        with self._pool.connection() as conn:
            row = conn.execute(
                """
                INSERT INTO engagement_events (
                    source, external_id, channel, kind, points, contact_ext,
                    company, account_id, campaign, occurred_at, raw
                ) VALUES (
                    %(source)s, %(external_id)s, %(channel)s, %(kind)s, %(points)s,
                    %(contact_ext)s, %(company)s, %(account_id)s, %(campaign)s,
                    %(occurred_at)s, %(raw)s
                )
                ON CONFLICT (source, external_id) DO UPDATE SET
                    points = EXCLUDED.points, contact_ext = EXCLUDED.contact_ext,
                    company = EXCLUDED.company, account_id = EXCLUDED.account_id,
                    campaign = EXCLUDED.campaign, occurred_at = EXCLUDED.occurred_at,
                    raw = EXCLUDED.raw
                RETURNING (xmax = 0) AS inserted
                """,
                {**r, "raw": Json(r["raw"])},
            ).fetchone()
        return bool(row["inserted"])

    def engaged_accounts(self) -> list[dict]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM engaged_accounts "
                "ORDER BY score DESC, last_touch DESC NULLS LAST"
            ).fetchall()
        return [dict(r) for r in rows]

    def events_for_account(self, account_id) -> list[dict]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM engagement_events WHERE account_id = %s "
                "ORDER BY occurred_at DESC",
                (account_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def contacts(self, *, account_id=None, unresolved_only=False) -> list[dict]:
        with self._pool.connection() as conn:
            if unresolved_only:
                rows = conn.execute(
                    "SELECT * FROM engagement_contacts WHERE account_id IS NULL "
                    "ORDER BY updated_at DESC"
                ).fetchall()
            elif account_id is not None:
                rows = conn.execute(
                    "SELECT * FROM engagement_contacts WHERE account_id = %s "
                    "ORDER BY updated_at DESC",
                    (account_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM engagement_contacts ORDER BY updated_at DESC"
                ).fetchall()
        return [dict(r) for r in rows]

    def recent_events(self, *, limit=200) -> list[dict]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM engagement_events ORDER BY occurred_at DESC LIMIT %s",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_sync_state(self, source=SOURCE_REPLYIO) -> dict | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM engagement_sync_state WHERE source = %s", (source,)
            ).fetchone()
        return dict(row) if row else None

    def set_sync_state(self, source=SOURCE_REPLYIO, *, status=None, stats=None, error=None,
                       window_from=None, window_to=None, last_synced_at=None) -> None:
        from psycopg.types.json import Json
        last = last_synced_at or datetime.now(UTC)
        with self._pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO engagement_sync_state
                    (source, last_synced_at, window_from, window_to, status, stats, error)
                VALUES (%(source)s, %(last)s, %(window_from)s, %(window_to)s,
                        %(status)s, %(stats)s, %(error)s)
                ON CONFLICT (source) DO UPDATE SET
                    last_synced_at = EXCLUDED.last_synced_at,
                    window_from = COALESCE(EXCLUDED.window_from, engagement_sync_state.window_from),
                    window_to = COALESCE(EXCLUDED.window_to, engagement_sync_state.window_to),
                    status = COALESCE(EXCLUDED.status, engagement_sync_state.status),
                    stats = COALESCE(EXCLUDED.stats, engagement_sync_state.stats),
                    error = EXCLUDED.error
                """,
                {"source": source, "last": last, "window_from": window_from,
                 "window_to": window_to, "status": status,
                 "stats": Json(stats) if stats is not None else None, "error": error},
            )

    def delete_all(self) -> int:
        with self._pool.connection() as conn:
            n = conn.execute("DELETE FROM engagement_events").rowcount or 0
            n += conn.execute("DELETE FROM engagement_contacts").rowcount or 0
            conn.execute("DELETE FROM engagement_raw")
            conn.execute("DELETE FROM engagement_sync_state")
        return n


# ── factory ───────────────────────────────────────────────────────────


def get_engagement_repository() -> EngagementRepository:
    """Postgres when DATABASE_URL is set; otherwise the JSON-file repo. Fails
    closed in production so real engagement data never lands silently in a file."""
    if os.getenv("DATABASE_URL"):
        return EngagementPostgresRepository()
    from auto_search.runtime import is_production
    if is_production():
        raise RuntimeError(
            "DATABASE_URL is required in production — refusing to run the engagement "
            "store on a JSON file."
        )
    return EngagementJsonRepository()
