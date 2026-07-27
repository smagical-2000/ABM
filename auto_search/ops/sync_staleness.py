"""Engagement sync staleness tripwire (2026-07-27).

The Jul 24→27 TOFU outage: the linkedin-tofu-cron Railway deployment silently
stopped ticking, and the Reply.io daily leg no-opped for 13 days on a missing
API key — while the daily digest printed both sources as "(success)" with an
ever-older timestamp. Nobody diffs dates by eye at 8:30am; a stale sync must
name itself. Same contract as source_streaks: one consolidated WARNING,
24h-throttled, weekend-aware, best-effort — computed from the same sync_state
rows the digest already prints.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from auto_search.ops.alerts import mark_ok, post_ops_alert, should_alert

logger = logging.getLogger(__name__)

ALERT_KIND = "sync-stale"

# Max healthy hours since last successful sync. linkedin_tofu does real work
# every ~6h inside its 13:00–23:00 UTC window, every day — the widest healthy
# gap (last evening run → next morning's first) is ~15h. The daily legs run
# weekdays at 12:30 UTC: ~24h midweek, Fri→Mon (72h) across a weekend.
THRESHOLDS_H: dict[str, float] = {
    "linkedin_tofu": 18.0,
    "sfdc": 26.0,
    "replyio": 26.0,
    "podcast": 26.0,
}
_WEEKDAY_SOURCES = {"sfdc", "replyio", "podcast"}
_MONDAY_H = 76.0  # Fri 12:30 → Mon 12:30 is 72h of healthy weekend silence

RUNBOOK = (
    "A source's sync has stopped landing. Check in order: (1) the cron's "
    "Railway deployment is actually scheduling ticks — `railway logs` must "
    "show a fresh `[run_*] rev …` block; a deployment can stop ticking after "
    "a bad redeploy (Jul 24→27 tofu outage: zero ticks, zero errors); "
    "(2) the service has the source's API key — a missing key is a silent "
    "no-op by design (replyio, Jul 14→27); (3) the source's own API/quota."
)


def _hours_since(value, now: datetime) -> float | None:
    """Hours between an ISO-ish timestamp and now; None when blank/unparseable.
    Naive stamps are treated as UTC (every writer stamps UTC)."""
    s = str(value or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace(" ", "T"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return max(0.0, (now - dt).total_seconds() / 3600.0)


def compute_staleness(erepo, *, now: datetime | None = None) -> list[dict]:
    """One row per tracked source: {source, hours, threshold, breached}.
    hours is None for a source that has NEVER synced — always a breach.
    Weekend-aware: weekday-cron sources don't breach on Sat/Sun (nothing is
    scheduled to run), and get the Fri→Mon allowance on Mondays."""
    now = now or datetime.now(UTC)
    out: list[dict] = []
    for source, base_threshold in THRESHOLDS_H.items():
        threshold = base_threshold
        skip = False
        if source in _WEEKDAY_SOURCES:
            if now.weekday() >= 5:  # Sat/Sun: the daily cron doesn't run
                skip = True
            elif now.weekday() == 0:  # Monday: allow the weekend gap
                threshold = _MONDAY_H
        st = (erepo.get_sync_state(source)
              if hasattr(erepo, "get_sync_state") else None) or {}
        hours = _hours_since(st.get("last_synced_at"), now)
        breached = (not skip) and (hours is None or hours > threshold)
        out.append({"source": source, "hours": hours,
                    "threshold": threshold, "breached": breached})
    return out


def _breach_line(s: dict) -> str:
    if s["hours"] is None:
        return f"{s['source']}: NEVER synced"
    return (f"{s['source']}: {s['hours']:.0f}h since last sync "
            f"(threshold {s['threshold']:.0f}h)")


def check_sync_staleness(erepo, *, alert: bool = True,
                         now: datetime | None = None) -> list[dict]:
    """Compute staleness and (optionally) post ONE consolidated breach alert.

    erepo doubles as the 24h-throttle state store (get/set_setting). Alerting
    failures never propagate — the digest run must not die on its tripwire —
    and an unavailable throttle state alerts UNTHROTTLED: this module exists
    to kill silence, so its failure mode is over-alerting, never under.
    """
    rows = compute_staleness(erepo, now=now)
    breaches = [s for s in rows if s["breached"]]
    if not alert:
        return rows

    if not breaches:
        # All clear: close any open incident so the NEXT breach alerts
        # immediately instead of waiting out a stale 24h stamp.
        try:
            mark_ok(erepo, ALERT_KIND)
        except Exception:  # noqa: BLE001
            logger.debug("sync-stale mark_ok failed", exc_info=True)
        return rows

    try:
        try:
            due = should_alert(erepo, ALERT_KIND, min_gap_hours=24)
        except Exception:  # noqa: BLE001 — throttle broken -> over-alert by contract
            logger.warning("sync-stale throttle unavailable — alerting unthrottled")
            due = True
        if due:
            post_ops_alert(
                kind=ALERT_KIND,
                title=f"{len(breaches)} engagement sync(s) stale",
                detail="\n".join(_breach_line(s) for s in breaches),
                service="run_digest",
                severity="warning",
                runbook=RUNBOOK,
            )
    except Exception:  # noqa: BLE001 — best-effort by contract
        logger.exception("sync-stale alert failed (rows still returned)")
    return rows
