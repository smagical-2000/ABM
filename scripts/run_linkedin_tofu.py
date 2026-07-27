"""Hourly LinkedIn TOFU ad-engagement run — the entry point a Railway cron calls.

Scrape reactions on Magical's sponsored posts -> dedup -> Apollo email -> capture
EVERY reactor (ABM match is a Yes/No flag since 2026-07-08, not a gate) -> upsert
into the "LinkedIn <> Airtable" table; Reply.io enrollment + `linkedin_tofu` heat
stay ABM-only. (SFDC creation is handled downstream by the Zapier Zap.)

DISABLED BY DEFAULT. A live run is a no-op unless LINKEDIN_TOFU_CRON_ENABLED=1, so
the cron service can be created/scheduled but will not write a thing until you flip
that env var on (after confirming the manual run looks right). `--dry-run` always
runs (no writes) regardless of the flag, for testing the wiring.

Needs DATABASE_URL, APIFY_API_KEY, APOLLO_API_KEY, REPLYIO_API_KEY,
AIRTABLE_API_KEY, AIRTABLE_BASE_ID, AIRTABLE_LINKEDIN_TABLE in the env.

Run:
    python scripts/run_linkedin_tofu.py --dry-run                  # safe, no writes
    LINKEDIN_TOFU_CRON_ENABLED=1 python scripts/run_linkedin_tofu.py   # live
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from auto_search.db import get_repository
from auto_search.db.engagement_repository import get_engagement_repository
from auto_search.db.scoring_repository import get_scoring_repository
from auto_search.engagement import linkedin_ads, linkedin_ads_runner
from auto_search.ops.logsetup import quiet_http_logs
from auto_search.ops.shutdown import close_pools, hard_exit

load_dotenv()   # no override: an operator-exported env (e.g. DATABASE_URL) must win
                # (2026-07-08: override=True let a local .env silently redirect a
                # "prod-env" run to the local dev DB → empty dedup → duplicate cards)
logger = logging.getLogger("run_linkedin_tofu")

_DEFAULT_CSV = (Path(__file__).resolve().parent.parent
                / "auto_search" / "engagement" / "linkedin_tofu_shares.csv")

_SYNC_SOURCE = "linkedin_tofu"   # sync_state key for the cost-guard throttle

# Every repository this run opened. Each one owns a psycopg ConnectionPool with
# worker threads, and this leg opens three — main() closes them in a finally so
# the process has nothing left to join on the way out (see ops/shutdown.py).
_OPENED: list = []


def _opened(repo):
    """Register a repo for shutdown, and return it."""
    _OPENED.append(repo)
    return repo


def _within_active_hours(now: datetime | None = None) -> bool:
    """Cost gate for a fast cadence: scan only during selling hours.

    The reactions actor re-bills a post's WHOLE reaction list on every scan, so
    the cadence is the cost multiplier — a 2am scan spends the same money for a
    Slack ping nobody reads. LINKEDIN_TOFU_ACTIVE_HOURS_UTC="13-23" (start
    inclusive, end exclusive) + LINKEDIN_TOFU_WEEKDAYS_ONLY=1 confine spend to
    the window. Unset window = always active (old behavior)."""
    now = now or datetime.now(UTC)
    if os.getenv("LINKEDIN_TOFU_WEEKDAYS_ONLY") == "1" and now.weekday() >= 5:
        return False
    window = (os.getenv("LINKEDIN_TOFU_ACTIVE_HOURS_UTC") or "").strip()
    if not window:
        return True
    try:
        start_s, end_s = window.split("-", 1)
        start, end = int(start_s), int(end_s)
    except (ValueError, TypeError):
        logger.warning("bad LINKEDIN_TOFU_ACTIVE_HOURS_UTC %r — ignoring", window)
        return True
    if start <= end:
        return start <= now.hour < end
    return now.hour >= start or now.hour < end   # window wraps midnight


def _crash_alert(repo, detail: str) -> None:
    """Slack ops alert for a failed run — throttled (3h) because this cron
    ticks every 15 min and a persistent break must not post 40 duplicates.
    Best-effort: alerting can never worsen the failure it reports."""
    try:
        from auto_search.ops import alerts
        if alerts.should_alert(repo, "tofu-cron", min_gap_hours=3.0):
            alerts.post_ops_alert(kind="tofu-cron", severity="failure",
                                  service="linkedin-tofu-cron",
                                  title="LinkedIn TOFU run FAILED", detail=detail)
    except Exception:  # noqa: BLE001
        logger.warning("ops crash alert failed (continuing)")


def _recovery_alert(repo) -> None:
    """One RECOVERED message when a run succeeds after crash alerts."""
    try:
        from auto_search.ops import alerts
        if alerts.mark_ok(repo, "tofu-cron"):
            alerts.post_ops_alert(kind="tofu-cron", severity="recovered",
                                  service="linkedin-tofu-cron",
                                  title="LinkedIn TOFU run green again")
    except Exception:  # noqa: BLE001
        logger.warning("ops recovery alert failed (continuing)")


def _stamp_attempt(repo, status: str, *, dry_run: bool, stats: dict | None = None) -> None:
    """Record that a REAL (spending) attempt happened, so the min-interval cost
    throttle counts it. Both outcomes stamp: the throttle guards SPEND, and a
    failed run has usually already paid Apify. `last_synced_at` is passed
    explicitly because set_sync_state only auto-stamps on success/failed — a
    future status string would silently leave it NULL (→ never throttles).
    Dry runs never spend, so they never arm the throttle. Best-effort: a stamp
    failure must not change the run's outcome."""
    if dry_run:
        return
    try:
        repo.set_sync_state(source=_SYNC_SOURCE, status=status, stats=stats,
                            last_synced_at=datetime.now(UTC))
    except Exception:  # noqa: BLE001
        logger.warning("sync-state stamp failed (throttle may not apply next tick)")


def _hours_since(ts) -> float | None:
    """Hours since an ISO/datetime timestamp, or None if unset/unparseable."""
    if not ts:
        return None
    try:
        dt = ts if isinstance(ts, datetime) else datetime.fromisoformat(
            str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return (datetime.now(UTC) - dt).total_seconds() / 3600
    except (ValueError, TypeError):
        return None


def main() -> int:
    """Thin wrapper so every exit path closes the pools this run opened."""
    try:
        return _run()
    finally:
        close_pools(_OPENED)
        _OPENED.clear()


def _run() -> int:
    ap = argparse.ArgumentParser(description="LinkedIn TOFU ad-engagement run")
    ap.add_argument("--dry-run", action="store_true",
                    help="no writes; ignores the enable flag (for testing)")
    ap.add_argument("--max-reactions", type=int, default=50, help="reactions per post")
    ap.add_argument("--max-contacts", type=int, default=None, help="people processed per run")
    ap.add_argument("--max-leads", type=int, default=None, help="stop after N leads")
    ap.add_argument("--csv", default=None, help="share_id,category CSV (default: packaged)")
    ap.add_argument("--allow-empty-store", action="store_true",
                    help="permit a LIVE run when the engagement store has no contacts "
                         "(only correct on a genuinely fresh deployment — an empty "
                         "dedup list re-posts every Slack card and re-bills enrichment)")
    ap.add_argument("--force", action="store_true",
                    help="bypass the min-interval cost throttle (manual immediate run)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    quiet_http_logs()   # httpx INFO prints the full request URL (secret hygiene)
    # Deploy-verification stamp: every tick prints the code revision it runs, so
    # "is the fix actually live?" is answered by the logs, never by deploy status.
    # (A stale Docker layer cache once served old code under a SUCCESS deploy.)
    print(f"[run_linkedin_tofu] rev {os.getenv('RAILWAY_GIT_COMMIT_SHA', 'unknown')[:9]} "
          f"build {os.getenv('BUILD_STAMP', 'unset')}", flush=True)
    # I6 fleet-parity heartbeat: a stale container names itself on first run.
    from auto_search.ops.heartbeat import beat
    beat("linkedin-tofu-cron")

    # The kill switch: a live run does nothing until the env flag is explicitly on.
    if not args.dry_run and os.getenv("LINKEDIN_TOFU_CRON_ENABLED") != "1":
        print("[run_linkedin_tofu] DISABLED — set LINKEDIN_TOFU_CRON_ENABLED=1 to run "
              "live, or pass --dry-run. No-op.", flush=True)
        return 0

    engagement_repo = _opened(get_engagement_repository())
    engagement_repo.ensure_schema()
    # Liveness stamp for the ops watchdog: EVERY tick (even a no-op) proves the
    # 15-min cron is alive; the watchdog alerts when this goes stale in-window.
    try:
        engagement_repo.set_setting("ops_tofu_last_tick", datetime.now(UTC).isoformat())
    except Exception:  # noqa: BLE001 — liveness stamping must never block the run
        logger.warning("ops tick stamp failed (continuing)")

    # Cost guard: the Railway cron ticks every 15 min, but ad reactions barely change and
    # re-scraping + re-enriching every tick was ~$12/day of Apify. Do the real work only
    # every LINKEDIN_TOFU_MIN_INTERVAL_HOURS (default 6); other ticks skip BEFORE any spend.
    # --dry-run and --force bypass it.
    if not (args.dry_run or args.force) and not _within_active_hours():
        print("[run_linkedin_tofu] outside active hours — no-op. (--force to override)",
              flush=True)
        return 0
    min_h = float(os.getenv("LINKEDIN_TOFU_MIN_INTERVAL_HOURS", "6"))
    if not (args.dry_run or args.force) and min_h > 0:
        last = (engagement_repo.get_sync_state(source=_SYNC_SOURCE) or {}).get("last_synced_at")
        hrs = _hours_since(last)
        if hrs is not None and hrs < min_h:
            print(f"[run_linkedin_tofu] throttled: last run {hrs:.1f}h ago (< {min_h}h) "
                  "— no-op. (--force to override)", flush=True)
            return 0

    csv_path = args.csv or os.getenv("LINKEDIN_TOFU_CSV") or str(_DEFAULT_CSV)
    share_categories = linkedin_ads.load_share_categories(Path(csv_path).read_text())
    if not share_categories:
        logger.error("no usable share_ids in %s", csv_path)
        _crash_alert(engagement_repo, f"no usable share_ids in {csv_path}")
        return 1

    airtable = reply = mirror = None
    if not args.dry_run:
        from auto_search.engagement.airtable_client import AirtableClient
        from auto_search.engagement.replyio_client import ReplyioClient
        airtable = AirtableClient()
        reply = ReplyioClient()
        # Tracking mirror (Galyna, 2026-07-08): dual-write every lead to the
        # "TOFU Leads by ABM" base. Unset env -> no mirror, primary unaffected.
        if os.getenv("AIRTABLE_TOFU_MIRROR_BASE_ID"):
            mirror = AirtableClient(
                base_id=os.environ["AIRTABLE_TOFU_MIRROR_BASE_ID"],
                table=os.getenv("AIRTABLE_TOFU_MIRROR_TABLE", "TOFU Leads by ABM"))
    try:
        summary = asyncio.run(linkedin_ads_runner.run(
            share_categories=share_categories, engagement_repo=engagement_repo,
            scoring_repo=_opened(get_scoring_repository()),
            discovery_repo=_opened(get_repository()),
            airtable_client=airtable, replyio_client=reply, mirror_client=mirror,
            max_reactions=args.max_reactions,
            max_contacts=args.max_contacts, max_leads=args.max_leads, dry_run=args.dry_run,
            allow_empty_store=args.allow_empty_store))
    except Exception:  # noqa: BLE001 — cron leg: log + signal failure, don't traceback-crash
        logger.exception("[run_linkedin_tofu] run failed")
        import traceback
        _crash_alert(engagement_repo, traceback.format_exc())
        # Stamp the FAILED attempt so the 6h cost throttle still applies. A crash
        # AFTER the paid Apify scrape (Airtable/Reply client construction, a DB
        # flap inside cross_and_persist) used to leave sync_state untouched, so
        # the next 15-min tick re-ran the whole paid scan — up to 4x/hour for the
        # rest of the active window, and the crash alert is throttled to 3h, so
        # the spend was quiet. --force is still the manual bypass.
        _stamp_attempt(engagement_repo, "failed", dry_run=args.dry_run)
        return 1
    _recovery_alert(engagement_repo)           # posts once iff a crash alert was open
    # Mirror health: the tracking table's whole job is proving nothing is
    # missed, so mirror write failures must be TOLD (throttled), and one
    # recovered note posts when it heals.
    mf = summary["stats"].get("mirror_failed", 0)
    if not args.dry_run and mf:
        try:
            from auto_search.ops import alerts
            if alerts.should_alert(engagement_repo, "tofu-mirror", min_gap_hours=3.0):
                alerts.post_ops_alert(
                    kind="tofu-mirror", severity="warning", service="linkedin-tofu-cron",
                    title=f"TOFU tracking mirror: {mf} lead(s) failed to write",
                    detail="Primary Airtable is unaffected. Check the API token's "
                           "access to the mirror base; the backfill script re-syncs.")
        except Exception:  # noqa: BLE001
            logger.warning("mirror alert failed (continuing)")
    elif not args.dry_run and summary["stats"].get("mirror_upserted"):
        try:
            from auto_search.ops import alerts
            if alerts.mark_ok(engagement_repo, "tofu-mirror"):
                alerts.post_ops_alert(kind="tofu-mirror", severity="recovered",
                                      service="linkedin-tofu-cron",
                                      title="TOFU tracking mirror healthy again")
        except Exception:  # noqa: BLE001
            logger.warning("mirror recovery alert failed (continuing)")
    _stamp_attempt(engagement_repo, "success", dry_run=args.dry_run,
                   stats=summary["stats"])
    print(f"[run_linkedin_tofu] {'dry-run ' if args.dry_run else ''}ok: {summary['stats']}",
          flush=True)
    # Event-driven handoff: this sync just WROTE new engagement events, so the
    # condition hit — push the tier-change notifier NOW instead of waiting for
    # the daily cron. The endpoint's notified_tiers ledger keeps it idempotent
    # (a like on an already-notified account posts nothing), and the push is a
    # no-op unless ENGAGEMENT_NOTIFY_ENABLED=1. Never fails the sync.
    if not args.dry_run and summary["stats"].get("heat_events", 0) > 0:
        print(f"[run_linkedin_tofu] {summary['stats']['heat_events']} new engagement "
              "event(s) — triggering tier-change notify", flush=True)
        rc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent / "run_engagement_notify.py")],
        ).returncode
        if rc:
            print(f"[run_linkedin_tofu] notify push rc={rc} (non-fatal)", flush=True)
    return 0


if __name__ == "__main__":
    # hard_exit, not sys.exit: interpreter finalization tries to join psycopg's
    # pool threads and can raise PythonFinalizationError / hang forever, leaving
    # a container Railway still counts as running — so the cron stops ticking.
    hard_exit(main())
