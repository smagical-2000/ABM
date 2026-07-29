"""Daily cron entry point — one scheduled run does both jobs:

    1. discovery scan   (run_discovery.py --days 1 --no-limit)   — job-posting + WARN
       + funding/leadership signals → qualify → panel
    2. social poll      (run_social.py --since-hours 24 …)        — Apify post-engagers
       on monitored accounts + event keywords → decision-maker filter → qualify → panel
    3. SFDC engagement  (run_engagement_sfdc.py)                  — read-only high-intent
       inbound leads → cross to scored/ABM → heat (idempotent; matched-only)
    4. Podcast leads    (run_engagement_podcast.py)               — read-only published
       CSV → cross to scored/ABM → heat (idempotent; no-ops without PODCAST_CSV_URL)
    5. Competitor news  (run_competitor_news.py)                  — Google News RSS
       distress scan on monitored competitors → news_items (fast-follower play)
    6. AE/SDR notify    (run_engagement_notify.py)                — after the syncs
       recompute tiers, fire NEW upward tier changes to Slack (kill-switched off by
       default; capped; ledger-deduped so nothing re-fires). Best-effort.
    7. Market news      (run_news.py)                             — the News tab's
       topic feed (same engine as the UI Refresh button; dedup by URL, only new
       articles enriched). Best-effort: before this it refreshed only when someone
       pressed the button (~3 refreshes all July).

Legs 1-5 run every time (one leg's failure never skips the others); the process
exits non-zero if ANY of them failed, so Railway flags the run. Leg 6 is
best-effort — a notify failure is logged but never fails the daily run (a Slack
hiccup must not mask a good sync), and it stays a no-op until explicitly enabled.

Error handling (2026-07-07, after a crash nobody was told about):
  - SELF-HEAL: a failed leg is retried once before it counts as failed —
    transient upstream blips (Apify, news feeds) usually pass on the second try.
  - ALERT: any leg still failing after the retry posts a Slack ops alert with
    the failing legs and the error tail; legs that recovered on retry post an
    informational note. Silence now means green, not "nobody looked".
  - STAMPS: `ops_daily_last_run` (every completion) and `ops_daily_last_ok`
    (green runs) feed the API watchdog, which alerts if this cron ever goes
    silent entirely. All reporting is best-effort — it can never break the run.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
# The repo root must be importable for _report (stamps + ops alerts): Python
# puts THIS script's dir on sys.path, not the root — in the cron container that
# meant `auto_search` failed to import and every stamp/alert silently died
# (found 2026-07-09 via the API watchdog's false "cron silent" alarm; the runs
# themselves were green). The leg scripts each self-bootstrap; so must we.
sys.path.insert(0, str(_SCRIPTS.parent))


def _run(script: str, *args: str) -> tuple[int, str]:
    """Run one leg. stdout streams straight to the Railway log; stderr (where
    tracebacks and logging go) is captured for the alert, then re-printed so
    the full log still lives in Railway."""
    print(f"\n=== {script} {' '.join(args)} ===", flush=True)
    try:
        # 1h hard cap per leg (review 2026-07-27): a leg hanging at interpreter
        # finalization (the Jul 24-27 class) must never wedge the whole daily
        # run — Railway would count it as still-active and stop scheduling.
        p = subprocess.run([sys.executable, str(_SCRIPTS / script), *args],
                           stderr=subprocess.PIPE, text=True, timeout=3600)
    except subprocess.TimeoutExpired as e:
        err = f"{script} exceeded the 1h leg cap — killed (likely hang)"
        if e.stderr:
            err += "\n" + (e.stderr if isinstance(e.stderr, str)
                           else e.stderr.decode(errors="replace"))
        sys.stderr.write(err + "\n")
        sys.stderr.flush()
        return 124, err
    if p.stderr:
        sys.stderr.write(p.stderr)
        sys.stderr.flush()
    return p.returncode, p.stderr or ""


def _leg(name: str, script: str, *args: str) -> tuple[int, str, bool]:
    """One leg with the one-shot retry. Returns (final_rc, error_tail,
    recovered_on_retry)."""
    rc, err = _run(script, *args)
    if rc == 0:
        return 0, "", False
    print(f"[run_daily] {name} failed (rc={rc}) — retrying once", flush=True)
    rc2, err2 = _run(script, *args)
    if rc2 == 0:
        return 0, "", True
    return rc2, err2, False


# Repos opened in-process (the legs themselves are subprocesses). Closed on the
# way out so nothing is left for the interpreter to join — see ops/shutdown.py.
_POOLS: list = []


def _report(failed: dict[str, str], recovered: list[str]) -> None:
    """Stamps + Slack alerts. Best-effort by construction: any exception here
    is swallowed — reporting must never change the run's outcome."""
    try:
        from datetime import UTC, datetime

        from auto_search.db.engagement_repository import get_engagement_repository
        from auto_search.ops import alerts

        repo = get_engagement_repository()
        _POOLS.append(repo)                     # closed by main()'s hard_exit
        now = datetime.now(UTC).isoformat()
        repo.set_setting("ops_daily_last_run", now)
        if not failed:
            repo.set_setting("ops_daily_last_ok", now)
        if failed:
            # gap 0: a daily job always alerts; the call also OPENS the
            # incident so the next green run posts exactly one RECOVERED.
            alerts.should_alert(repo, "daily-cron", min_gap_hours=0)
            tail = next(iter(failed.values()))[-1200:]
            alerts.post_ops_alert(
                kind="daily-cron", severity="failure", service="discovery-cron",
                title=f"Daily run FAILED: {', '.join(failed)}",
                detail=tail or "no stderr captured — see Railway logs")
        elif recovered:
            alerts.post_ops_alert(
                kind="daily-cron", severity="warning", service="discovery-cron",
                title=f"Daily run OK after retry: {', '.join(recovered)} "
                      "recovered on the second attempt")
        if not failed and alerts.mark_ok(repo, "daily-cron") and not recovered:
            alerts.post_ops_alert(kind="daily-cron", severity="recovered",
                                  service="discovery-cron",
                                  title="Daily run green again")
    except Exception:  # noqa: BLE001 — reporting is best-effort, never fatal
        import logging
        logging.getLogger(__name__).exception("run_daily reporting failed")


def _heartbeat(service: str) -> None:
    """I6 fleet-parity heartbeat (shared impl in ops/heartbeat.py): a stale
    container betrays itself on its FIRST run, by name — not 10 days later via
    a twin it minted (the 2026-07-10→20 stale tofu-cron)."""
    from auto_search.ops.heartbeat import beat
    beat(service)


def main() -> int:
    # Deploy verification: Railway "SUCCESS" is not proof this code is running —
    # the stamp printed here must match the .build-stamp shipped with the deploy.
    import os
    print(f"[run_daily] rev {os.getenv('RAILWAY_GIT_COMMIT_SHA', '?')[:9]} "
          f"build {os.getenv('BUILD_STAMP', '?')}", flush=True)
    _heartbeat("discovery-cron")
    legs = (("discovery", "run_discovery.py", "--days", "1", "--no-limit"),
            ("social", "run_social.py", "--since-hours", "24", "--max-enrich", "100"),
            ("sfdc", "run_engagement_sfdc.py", "--since", "2026-01-01"),
            # Reply.io was never a daily leg — its heat froze whenever nobody ran
            # it by hand (last manual sync 2026-07-14; found 2026-07-20).
            ("replyio", "run_engagement_replyio.py"),
            ("podcast", "run_engagement_podcast.py"),
            ("competitor", "run_competitor_news.py"),
            # Reconcile: diff the last 14 days of SFDC leads/meetings against the
            # engagement store and ALERT on anything we silently missed — label
            # drift ('CS Headspace | BOFU', 'TOFU Engagement Campaign') gets
            # caught in 24h instead of weeks.
            ("reconcile", "run_reconcile_sfdc.py"))
    failed: dict[str, str] = {}
    recovered: list[str] = []
    for name, script, *args in legs:
        rc, err, healed = _leg(name, script, *args)
        if rc:
            failed[name] = err
        elif healed:
            recovered.append(name)
    # Best-effort AE/SDR handoff — runs AFTER the syncs so tiers are current. A Slack
    # failure here must NOT fail the daily run, so its rc is logged, not gated on.
    notify_rc, _, _ = _leg("notify", "run_engagement_notify.py")

    # Best-effort market-news refresh — the News tab feeds itself daily instead of
    # waiting on someone to press Refresh. Free RSS + a cents-sized enrich pass over
    # new URLs only; a feed hiccup must not fail the daily run.
    news_rc, _, _ = _leg("news", "run_news.py")
    if news_rc:
        print(f"\n[run_daily] news refresh rc={news_rc} (non-fatal)", flush=True)

    # Best-effort: keep Galyna's tracking table ("ABM Flow LinkedIn <> Airtable")
    # in sync with funnel leads that arrive via the CLAY path — our runner
    # dual-writes its own captures live, but a Clay-dump row with an email
    # becomes an SFDC lead invisibly to us (Lynn Osgood, 2026-07-08). The
    # reconcile is upsert-only and additive; it never deletes.
    if os.getenv("AIRTABLE_TOFU_MIRROR_BASE_ID"):
        tracking_rc, _, _ = _leg("tracking", "backfill_tofu_mirror.py", "--apply")
        if tracking_rc:
            print(f"\n[run_daily] tracking reconcile rc={tracking_rc} (non-fatal)", flush=True)
    else:
        print("\n[run_daily] tracking reconcile skipped (mirror env unset)", flush=True)

    # Daily digest LAST — one card summarizing what ingested / scored / is due /
    # was held and why, so silence stops being ambiguous (a missing digest is
    # itself the alarm). Best-effort like notify.
    digest_rc, _, _ = _leg("digest", "run_digest.py")
    if digest_rc:
        print(f"\n[run_daily] digest rc={digest_rc} (non-fatal)", flush=True)

    _report(failed, recovered)
    if failed:
        print(f"\n[run_daily] FAILED — {', '.join(failed)} (after retry)", flush=True)
        return 1
    if notify_rc:
        print(f"\n[run_daily] syncs OK; notify leg rc={notify_rc} (non-fatal)", flush=True)
    print("\n[run_daily] all legs OK", flush=True)
    return 0


if __name__ == "__main__":
    # run_entrypoint (os._exit), not sys.exit: interpreter finalization tries to
    # join psycopg's pool threads and can raise PythonFinalizationError / hang
    # forever, leaving a container Railway still counts as running — so the cron
    # stops ticking. It hard-exits even if main() raises.
    from auto_search.ops.shutdown import run_entrypoint
    run_entrypoint(main, pools=_POOLS)
