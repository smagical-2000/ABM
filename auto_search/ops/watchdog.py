"""Ops watchdog — the always-on API process checks, every 15 minutes, that the
scheduled crons actually RAN when they should have, and alerts when one went
silent. This is the layer in-process error alerts can't provide: a cron whose
process never starts (broken deploy, unscheduled service, platform outage)
produces no error anywhere — only absence. The watchdog turns absence into a
Slack message.

What it watches (stamps written by the crons themselves):
  - `ops_daily_last_ok`   — run_daily stamps on a fully-green run.
                            Expected every weekday at 14:00 UTC (+2h grace).
  - `ops_tofu_last_tick`  — run_linkedin_tofu stamps at EVERY invocation
                            (even out-of-window no-ops), proving the 15-min
                            cron is alive. Checked only inside weekday selling
                            hours (13-23 UTC) with a 45-min grace.

Checks are PURE functions (now + stamp -> overdue reason or None) so the
calendar math is fully testable. The loop wires them to alerts.should_alert /
mark_ok, so an incident alerts once every RE_ALERT_HOURS, then posts one
RECOVERED when the stamp goes fresh again. Enabled only where OPS_WATCHDOG=1
(one service, no duplicate alerting).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta

from auto_search.ops import alerts

logger = logging.getLogger(__name__)

DAILY_HOUR_UTC = 14          # discovery-cron schedule (Mon-Fri 14:00 UTC)
DAILY_GRACE_H = 2.0
TOFU_START_UTC, TOFU_END_UTC = 13, 23   # selling-hours window the runner uses
TOFU_GRACE_MIN = 45
RE_ALERT_HOURS = 6.0         # while an incident stays open, re-alert this often
INTERVAL_S = 900


def _parse(ts: str | None) -> datetime | None:
    try:
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def overdue_daily(last_ok: str | None, now: datetime) -> str | None:
    """Reason the daily run is overdue, or None. Expected: the most recent
    weekday 14:00 UTC that is at least DAILY_GRACE_H in the past (so a Monday
    morning check doesn't call the weekend a miss)."""
    expected = None
    for back in range(8):
        d = now - timedelta(days=back)
        if d.weekday() >= 5:                                   # Sat/Sun: no run
            continue
        candidate = d.replace(hour=DAILY_HOUR_UTC, minute=0, second=0, microsecond=0)
        if candidate <= now - timedelta(hours=DAILY_GRACE_H):
            expected = candidate
            break
    if expected is None:                                       # week just started
        return None
    last = _parse(last_ok)
    if last is None or last < expected:
        seen = last.strftime("%b %d %H:%M UTC") if last else "never"
        return (f"no successful daily run since {seen}; expected one at "
                f"{expected.strftime('%b %d %H:%M UTC')}")
    return None


def stale_tofu(last_tick: str | None, now: datetime) -> str | None:
    """Reason the 15-min LinkedIn cron looks dead, or None. Only meaningful
    inside weekday selling hours, after a grace period into the window."""
    if now.weekday() >= 5 or not (TOFU_START_UTC <= now.hour < TOFU_END_UTC):
        return None
    window_start = now.replace(hour=TOFU_START_UTC, minute=0, second=0, microsecond=0)
    if (now - window_start) < timedelta(minutes=TOFU_GRACE_MIN):
        return None
    last = _parse(last_tick)
    if last is None or (now - last) > timedelta(minutes=TOFU_GRACE_MIN):
        seen = last.strftime("%b %d %H:%M UTC") if last else "never"
        return (f"linkedin-tofu cron has not ticked since {seen} "
                f"(> {TOFU_GRACE_MIN} min inside selling hours)")
    return None


_CHECKS = (
    ("daily-cron-silent", "Daily discovery cron did not run", "ops_daily_last_ok",
     overdue_daily),
    ("tofu-cron-silent", "LinkedIn 15-min cron is not ticking", "ops_tofu_last_tick",
     stale_tofu),
)


def run_checks(repo, now: datetime | None = None) -> list[dict]:
    """One watchdog pass. Returns what it did (for logs/tests): each entry is
    {check, status: alerted|recovered|ok|quiet}. `quiet` = overdue but inside
    the re-alert gap."""
    now = now or datetime.now(UTC)
    actions = []
    for key, title, setting, check in _CHECKS:
        try:
            stamp = repo.get_setting(setting)
        except Exception:  # noqa: BLE001 — a broken repo read is itself alert-worthy
            stamp = None
        reason = check(stamp, now)
        if reason:
            if alerts.should_alert(repo, key, min_gap_hours=RE_ALERT_HOURS, now=now):
                alerts.post_ops_alert(kind=key, title=title, detail=reason,
                                      service="watchdog", severity="failure")
                actions.append({"check": key, "status": "alerted", "reason": reason})
            else:
                actions.append({"check": key, "status": "quiet", "reason": reason})
        elif alerts.mark_ok(repo, key):
            alerts.post_ops_alert(kind=key, title=title + " — recovered",
                                  service="watchdog", severity="recovered")
            actions.append({"check": key, "status": "recovered"})
        else:
            actions.append({"check": key, "status": "ok"})
    return actions


async def watchdog_loop(app) -> None:
    """Background task for the API lifespan. Never lets an exception kill the
    loop — a watchdog that can die silently would be the joke writing itself."""
    # print, not logger: the prod app configures no logging handlers, so this
    # boot line must go to stdout to be visible in Railway (deploy verification).
    print(f"[ops-watchdog] running every {INTERVAL_S}s "
          f"(build {os.getenv('BUILD_STAMP', '?')})", flush=True)
    while True:
        try:
            repo = getattr(app.state, "engagement_repo", None)
            if repo is not None:
                acted = [a for a in run_checks(repo) if a["status"] != "ok"]
                if acted:
                    logger.info("ops watchdog: %s", acted)
        except Exception:  # noqa: BLE001
            logger.exception("ops watchdog pass failed (loop continues)")
        await asyncio.sleep(INTERVAL_S)


def enabled() -> bool:
    return os.getenv("OPS_WATCHDOG") == "1"
