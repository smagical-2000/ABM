"""Zero-streak tripwire — makes a silently dead discovery source impossible.

Born from the 2026-07-23 live source audit (MAR2-45): half the discovery
sources had produced NOTHING for 3+ weeks — the SignalBase `positions` feed
collapsed ~Jul 1, warntracker served a frozen April sample, every event
attendee died at the is_us() gate, own-post engagers aged out of the 24h
window — and no alert fired, because each failure mode looked like a
quiet-but-green run (exit 0, rows fetched, nothing yielded). Per-run error
alerting can never catch that class; "days since the source last produced a
NEW company" is the one metric every such mode moves.

check_streaks() computes that streak per source (a company's FIRST signal
attributes it to the source that discovered it — the same
discovery_signals ⋈ discovery_companies join the audit ran), compares against
per-source cadence thresholds, and posts ONE consolidated ops alert covering
every breach, throttled to one per 24h via alerts.should_alert. It is wired as
a best-effort step in scripts/run_digest.py so it runs every weekday with no
new cron; the digest card also carries the one-line summary.

Accepts either discovery repo (Postgres via its pool, JSON via its store) or a
raw psycopg connection — there is no public repo query for this, and adding
one to the protocol for a single ops consumer wasn't worth the surface.

ATTRIBUTION (2026-07-27): a connector's declared `source_name` is not always the
`source` its signals persist under, and the streak must speak the CONNECTOR's
name — the one in THRESHOLDS, connector_runs and the digest. SOURCE_ALIASES
collapses the difference (jobs → 'indeed'/'linkedin'); add an entry whenever a
new connector tags signals per sub-feed, or its streak silently reads "NEVER".
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from auto_search.normalize import parse_iso_datetime
from auto_search.ops.alerts import mark_ok, post_ops_alert, should_alert

logger = logging.getLogger(__name__)

ALERT_KIND = "source-silence"
RUNBOOK = ("per-source triage lives in the 2026-07-23 live source audit "
           "(MAR2-45) — check the connector's server window/filters first; "
           "'quiet market' was the wrong diagnosis every single time.")

# Days-silent threshold per source, tuned to each source's natural cadence so
# the alert means "dead", not "slow week":
#   • jobs / competitor posts land most weekdays → 3 WEEKDAYS (weekend-proof —
#     a Monday run must not page about Saturday's silence);
#   • the rare event feeds (WARN, leadership, M&A, funding) emit a handful of
#     qualifying US-healthcare events per week → 10 calendar days;
#   • own posts follow OUR posting cadence and events follow conference
#     season — both legitimately sparse → 14 calendar days.
#
# The keys here are CONNECTOR names (what an operator reads in the digest and in
# connector_runs), which is not always what a signal is tagged with in the DB —
# see SOURCE_ALIASES below.
THRESHOLDS: dict[str, int] = {
    "jobs": 3,
    # Per-board jobs rows (review 2026-07-27): the 'jobs' collapse alone hides
    # a single board dying while its sibling produces (indeed was ~47% of all
    # jobs discoveries). Wider than the aggregate so one slow board doesn't
    # double-page, but a dead one names itself within a week.
    "indeed": 5,
    "linkedin": 5,
    "social_competitor_post": 3,
    "warntracker": 10,
    "signalbase_leadership": 10,
    "signalbase_acquisitions": 10,
    "signalbase_funding": 10,
    "social_magical_post": 14,
    "social_event": 14,
}
_WEEKDAY_SOURCES = frozenset({"jobs", "indeed", "linkedin",
                              "social_competitor_post"})

# Persisted discovery_signals.source -> the THRESHOLDS key it belongs to.
# JobPostingsConnector declares source_name='jobs' (that is what connector_runs
# and the digest show), but the RawSignals it emits are tagged per BOARD —
# source='indeed' / 'linkedin'. So last_by_source['jobs'] was None FOREVER and
# the digest read "jobs: NEVER produced a company" every single day, while jobs
# was the platform's top producer (prod 2026-07-27: 393 companies first
# attributed to 'indeed', 446 to 'linkedin'; newest Jul-24). A tripwire that
# always fires is a tripwire nobody reads — that is what this collapse fixes.
SOURCE_ALIASES: dict[str, str] = {"indeed": "jobs", "linkedin": "jobs"}

# The jobs stacking gate parks lone STANDARD hires on the watch ledger instead
# of creating a discovery_companies row, so a healthy run can legitimately
# qualify nobody. `first_parked_at` (a NEW park — re-parking an existing company
# only bumps last_seen_at) is that run's proof of productivity, so it floors the
# jobs streak. Jobs only: no other source parks.
_PARK_FLOOR_SOURCE = "jobs"

# A company's first signal (earliest ingested) names the source that DISCOVERED
# it; per source, the newest such company is the end of its streak. dict_row
# pool → keyed access.
_SQL_LAST_NEW = """
    SELECT f.source AS source, MAX(f.first_seen_at) AS last_new
      FROM (SELECT DISTINCT ON (s.company_id)
                   s.company_id, s.source, c.first_seen_at
              FROM discovery_signals s
              JOIN discovery_companies c ON c.id = s.company_id
             ORDER BY s.company_id, s.ingested_at ASC, s.id ASC) f
     GROUP BY f.source
"""


_SQL_LAST_PARK = "SELECT MAX(first_parked_at) FROM parked_companies"


def _collapse(raw: dict[str, datetime]) -> dict[str, datetime]:
    """Fold per-board source keys onto their connector key (newest wins), so the
    streak reads the same names THRESHOLDS and the digest do. The per-board
    keys are KEPT alongside the collapsed one (review 2026-07-27): a single
    board dying while its sibling produces would otherwise be invisible —
    'jobs' would stay fresh forever while half the pipeline is dark. The
    per-board rows have their own (wider) THRESHOLDS entries."""
    out: dict[str, datetime] = dict(raw)
    for src, t in raw.items():
        key = SOURCE_ALIASES.get(src, src)
        if key != src and (key not in out or t > out[key]):
            out[key] = t
    return out


def last_park(repo_or_conn) -> datetime | None:
    """When the jobs gate last parked a NEW company (aware UTC), or None.

    Best-effort: a repo/connection without a watch ledger simply has no floor —
    the streak then falls back to discovery_companies alone."""
    fn = getattr(repo_or_conn, "parked_companies", None)
    if callable(fn):
        try:
            times = [parse_iso_datetime(r.get("first_parked_at"))
                     for r in fn()]
        except Exception:  # noqa: BLE001 — the floor is a bonus, never a failure
            logger.debug("parked_companies unavailable for streak floor", exc_info=True)
            return None
        times = [t for t in times if t]
        return max(times) if times else None

    if hasattr(repo_or_conn, "execute"):          # raw psycopg connection
        try:
            row = repo_or_conn.execute(_SQL_LAST_PARK).fetchone()
        except Exception:  # noqa: BLE001
            logger.debug("parked_companies query failed", exc_info=True)
            return None
        val = (row.get("max") if isinstance(row, dict) else row[0]) if row else None
        return _as_utc(val) if val else None
    return None


def last_new_by_source(repo_or_conn) -> dict[str, datetime]:
    """When each source last produced a NEW company (aware UTC), keyed by the
    CONNECTOR name (board sources collapsed via SOURCE_ALIASES). Sources that
    never produced are absent."""
    pool = getattr(repo_or_conn, "_pool", None)
    if pool is not None:                          # PostgresRepository
        with pool.connection() as conn:
            rows = conn.execute(_SQL_LAST_NEW).fetchall()
        return _collapse({r["source"]: _as_utc(r["last_new"]) for r in rows
                          if r.get("last_new") is not None})

    store = getattr(repo_or_conn, "_store", None)
    if store is not None:                         # JsonFileRepository
        # Signals are append-ordered (save_candidate/add_signal), so
        # signals[0] is the signal that created the company row.
        out: dict[str, datetime] = {}
        for row in store.values():
            sigs = row.get("signals") or []
            src = (sigs[0].get("source") if sigs else None)
            t = parse_iso_datetime(row.get("first_seen_at"))
            if src and t and (src not in out or t > out[src]):
                out[src] = t
        return _collapse(out)

    if hasattr(repo_or_conn, "execute"):          # raw psycopg connection
        rows = repo_or_conn.execute(_SQL_LAST_NEW).fetchall()
        return _collapse({r[0]: _as_utc(r[1]) for r in rows if r[1] is not None})

    raise TypeError(
        f"unsupported repo/connection for streak check: {type(repo_or_conn)!r}")


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _weekdays_between(start: datetime, end: datetime) -> int:
    """Mon–Fri days elapsed from start to end (calendar-date based, so a source
    that produced Friday reads 1 on Monday, not 3)."""
    if end <= start:
        return 0
    days = (end.date() - start.date()).days
    full_weeks, rem = divmod(days, 7)
    n = full_weeks * 5
    wd = start.date().weekday()
    for i in range(rem):
        if (wd + 1 + i) % 7 < 5:
            n += 1
    return n


def compute_streaks(repo_or_conn, *, now: datetime | None = None) -> list[dict]:
    """One row per tracked source: {source, last_new_at, days_silent, unit,
    threshold, breached}. days_silent is None for a source that has NEVER
    produced a company — the worst streak there is, so it always breaches."""
    now = now or datetime.now(UTC)
    last_by_source = last_new_by_source(repo_or_conn)
    parked_at = last_park(repo_or_conn)
    if parked_at is not None:
        prev = last_by_source.get(_PARK_FLOOR_SOURCE)
        if prev is None or parked_at > prev:
            last_by_source[_PARK_FLOOR_SOURCE] = parked_at
    out: list[dict] = []
    for source, threshold in THRESHOLDS.items():
        last = last_by_source.get(source)
        unit = "wd" if source in _WEEKDAY_SOURCES else "d"
        if last is None:
            days: int | None = None
            breached = True
        else:
            days = (_weekdays_between(last, now) if unit == "wd"
                    else max(0, (now - last).days))
            breached = days >= threshold
        out.append({"source": source, "last_new_at": last, "days_silent": days,
                    "unit": unit, "threshold": threshold, "breached": breached})
    return out


def _breach_line(s: dict) -> str:
    if s["days_silent"] is None:
        return f"{s['source']}: NEVER produced a company"
    return (f"{s['source']}: {s['days_silent']}{s['unit']} since last new "
            f"company (threshold {s['threshold']}{s['unit']})")


def format_digest_line(streaks: list[dict]) -> str:
    """One digest-card line: all-fresh, or the breaching sources by name."""
    breaches = [s for s in streaks if s["breached"]]
    if not breaches:
        return (f"• discovery sources: all {len(streaks)} fresh "
                "(within cadence thresholds)")
    named = ", ".join(
        f"{s['source']} {'never' if s['days_silent'] is None else str(s['days_silent']) + s['unit']}"
        for s in breaches)
    return f"• discovery sources SILENT ({len(breaches)}/{len(streaks)}): {named}"


def check_streaks(repo_or_conn, *, alert: bool = True, state_repo=None,
                  now: datetime | None = None) -> list[dict]:
    """Compute streaks and (optionally) post ONE consolidated breach alert.

    `state_repo` holds the 24h throttle state (needs get_setting/set_setting —
    the engagement repos do; run_digest passes its erepo). No state repo
    available → alert WITHOUT throttling: this module exists to kill silence,
    so its failure mode is over-alerting, never under (same call alerts.py
    makes). Alerting failures never propagate — the digest run must not die
    on its tripwire.
    """
    streaks = compute_streaks(repo_or_conn, now=now)
    breaches = [s for s in streaks if s["breached"]]
    if not alert:
        return streaks
    try:
        if state_repo is None:
            from auto_search.db.engagement_repository import (
                get_engagement_repository,
            )
            state_repo = get_engagement_repository()
    except Exception:  # noqa: BLE001 — throttle state is optional, alerts are not
        logger.warning("source-streak throttle state unavailable — alerting unthrottled")
        state_repo = None

    if not breaches:
        # All clear: close any open incident so the NEXT breach alerts
        # immediately instead of waiting out a stale 24h stamp.
        if state_repo is not None:
            try:
                mark_ok(state_repo, ALERT_KIND)
            except Exception:  # noqa: BLE001
                logger.debug("source-streak mark_ok failed", exc_info=True)
        return streaks

    try:
        due = True if state_repo is None else should_alert(
            state_repo, ALERT_KIND, min_gap_hours=24)
        if due:
            post_ops_alert(
                kind=ALERT_KIND,
                title=f"{len(breaches)} discovery source(s) silent",
                detail="\n".join(_breach_line(s) for s in breaches),
                service="run_digest",
                severity="warning",
                runbook=RUNBOOK,
            )
    except Exception:  # noqa: BLE001 — best-effort by contract
        logger.exception("source-streak alert failed (streaks still returned)")
    return streaks
