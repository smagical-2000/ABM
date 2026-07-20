"""Fleet heartbeat — each cron/service records its BUILD_STAMP on run start.

The I6-fleet audit invariant compares these against the API's own stamp, so a
stale container (deployed but never rebuilt — the 2026-07-10→20 linkedin-tofu-cron
that kept minting twins for ten days) betrays itself on its FIRST run, by name.
Best-effort by design: a heartbeat failure must never fail the job it stamps.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

_KEY = "service_stamps"


def beat(service: str, repo=None) -> bool:
    """Record {service: {stamp, at}} in engagement settings. True if written."""
    try:
        if repo is None:
            from auto_search.db.engagement_repository import get_engagement_repository
            repo = get_engagement_repository()
        stamps = json.loads(repo.get_setting(_KEY) or "{}")
        stamps[service] = {"stamp": os.getenv("BUILD_STAMP", "unset"),
                           "at": datetime.now(UTC).isoformat()}
        repo.set_setting(_KEY, json.dumps(stamps))
        return True
    except Exception as e:  # noqa: BLE001 — never fail the run being stamped
        logger.warning("heartbeat for %s skipped: %s", service, e)
        return False


def read_stamps(repo=None) -> dict:
    """The recorded {service: {stamp, at}} map. Tolerant by design: a missing
    repo, a corrupt setting, or a non-dict payload all read as {} — the readers
    (audit I6, the daily digest) must never crash on the fleet ledger."""
    try:
        if repo is None:
            from auto_search.db.engagement_repository import get_engagement_repository
            repo = get_engagement_repository()
        stamps = json.loads(repo.get_setting(_KEY) or "{}")
        return stamps if isinstance(stamps, dict) else {}
    except Exception as e:  # noqa: BLE001 — a broken ledger reads as empty
        logger.warning("heartbeat read skipped: %s", e)
        return {}


def stale_writers(stamps: dict, own_stamp: str) -> dict:
    """Services whose LAST beat came AFTER the API's own beat but on a DIFFERENT
    build — {service: stamp}. Anchored to the API's recorded beat (the 'api'
    entry, written at app boot) instead of a wall-clock window, so a routine
    deploy's lingering pre-deploy beats can never false-red healthy crons: a
    writer is stale only if it ran on old code AFTER the new API came up.
    No 'api' beat yet → {} (the invariant has no anchor to judge against).
    Beats stamped 'unset' (local/dev) are never flagged."""
    api_at = str(((stamps or {}).get("api") or {}).get("at") or "")
    if not api_at:
        return {}
    stale: dict[str, str] = {}
    for svc, rec in (stamps or {}).items():
        if svc == "api":
            continue
        at = str((rec or {}).get("at") or "")
        st = str((rec or {}).get("stamp") or "").strip()
        if at > api_at and st and st not in ("unset", own_stamp):
            stale[svc] = st
    return stale
