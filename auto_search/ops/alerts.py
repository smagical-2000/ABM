"""Ops alerts — a Slack message the moment an automation breaks (and when it
recovers), so failures are TOLD to the team instead of waiting to be noticed
on a dashboard. Born from the 2026-07-07 daily-cron crash that nobody knew
about until someone happened to read the Railway logs.

Three producers use this module:
  1. Cron entrypoints (run_daily, run_linkedin_tofu) post a FAILURE alert when
     a run fails — after a one-shot retry gave it a chance to self-heal.
  2. The same entrypoints post one RECOVERED message when a previously-failing
     job succeeds again (state via mark_ok, so recovery never spams).
  3. The API watchdog (ops/watchdog.py) alerts when a cron didn't run AT ALL —
     the silent case an in-process alert can never catch.

Pattern matches ops/changelog.py: `build_alert_card` is PURE (testable, no
I/O); `post_ops_alert` does the one httpx POST and NEVER raises — a Slack
hiccup must not break the job it reports on. Destination is
SLACK_OPS_ALERTS_WEBHOOK, falling back to the private SLACK_ENGAGEMENT_WEBHOOK
so alerts always have somewhere to land before a dedicated channel exists.
Throttle/recovery state lives in the caller's repo settings (JSON under
`ops_alert_state`), so a 15-minute cron can't post 96 identical alerts a day.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime

import httpx

logger = logging.getLogger(__name__)

_STATE_KEY = "ops_alert_state"
_SEVERITY_HEAD = {"failure": "ALERT", "warning": "WARNING", "recovered": "RECOVERED"}


def build_alert_card(*, kind: str, title: str, detail: str = "", service: str = "",
                     build: str = "", severity: str = "failure",
                     now: datetime | None = None) -> dict:
    """Pure: one ops event -> a Slack Block Kit payload. `kind` is the stable
    incident key (e.g. 'daily-cron'); `detail` (error tail) renders as code."""
    head = _SEVERITY_HEAD.get(severity, "ALERT")
    ts = (now or datetime.now(UTC)).strftime("%b %d, %H:%M UTC")
    sections: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": f"[{head}] {title}"[:150]}}]
    if detail:
        sections.append({"type": "section", "text": {
            "type": "mrkdwn", "text": f"```{detail[-1500:]}```"}})
    ctx = " · ".join(x for x in (kind, service, f"build {build}" if build else "", ts) if x)
    sections.append({"type": "context", "elements": [{"type": "mrkdwn", "text": ctx}]})
    return {"blocks": sections}


def post_ops_alert(*, kind: str, title: str, detail: str = "", service: str = "",
                   severity: str = "failure", webhook: str | None = None) -> bool:
    """Post one alert. True on a 2xx. No webhook configured -> log + False;
    never raises (the job being reported on must not die reporting)."""
    hook = (webhook or os.getenv("SLACK_OPS_ALERTS_WEBHOOK")
            or os.getenv("SLACK_ENGAGEMENT_WEBHOOK"))
    if not hook:
        logger.warning("ops alert (no webhook configured, not posted): [%s] %s", kind, title)
        return False
    card = build_alert_card(kind=kind, title=title, detail=detail, service=service,
                            build=os.getenv("BUILD_STAMP", ""), severity=severity)
    try:
        r = httpx.post(hook, json=card, timeout=15)
        r.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001 — alerting must never break the alerted job
        logger.warning("ops alert post failed for %s: %s", kind, e)
        return False


# ── throttle / recovery state (repo settings) ─────────────────────────
# repo must expose get_setting/set_setting (both engagement repos do). Every
# state error answers "fail open": if the DB is broken we'd rather over-alert
# than stay silent — silence is the failure mode this module exists to kill.


def _load_state(repo) -> dict:
    try:
        return json.loads(repo.get_setting(_STATE_KEY) or "{}")
    except Exception:  # noqa: BLE001
        return {}


def _save_state(repo, state: dict) -> None:
    try:
        repo.set_setting(_STATE_KEY, json.dumps(state))
    except Exception:  # noqa: BLE001
        logger.warning("ops alert state save failed (continuing)")


def should_alert(repo, key: str, *, min_gap_hours: float = 3.0,
                 now: datetime | None = None) -> bool:
    """True if `key` may alert now (first failure, or the gap has passed).
    Marks the incident open and stamps the send time when it returns True."""
    now = now or datetime.now(UTC)
    state = _load_state(repo)
    entry = state.get(key)
    if entry:
        try:
            last = datetime.fromisoformat(entry.get("last"))
            if (now - last).total_seconds() < min_gap_hours * 3600:
                return False
        except (TypeError, ValueError):
            pass  # corrupt stamp -> treat as due
    state[key] = {"since": (entry or {}).get("since") or now.isoformat(),
                  "last": now.isoformat()}
    _save_state(repo, state)
    return True


def mark_ok(repo, key: str) -> bool:
    """Clear an open incident. True exactly once per incident (the caller
    posts the single RECOVERED message on True); False when nothing was open."""
    state = _load_state(repo)
    if key not in state:
        return False
    del state[key]
    _save_state(repo, state)
    return True
