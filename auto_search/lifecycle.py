"""Self-cleaning lead lifecycle — the TTL decay that keeps Discovery from filling
with low-intent leads.

Reads qualified / needs-review companies (with their signals) from the discovery
repo, recomputes buying intent, and moves stale ones down the chain:

    Watch         no new signal for WATCH_TTL days        ->  Needs review
    Needs review  no new signal for +REVIEW_TTL days more ->  auto-rejected

The decay clock is "time since the freshest signal" (priority.last_signal_at), so
a new stacking signal both re-scores intent (can jump straight to Hot) AND resets
the clock — the escape hatch beats the timer. Hot/Watch tiering is computed live
in the panel; this sweep only performs the time-based transitions, so it runs once
a day from the discovery cron.

Both transitions reuse existing state: Watch->Needs review is an icp_status flip
(qualified -> needs_review), and the auto-reject is review_status='rejected'
(icp_status untouched), so /restore brings an aged-out lead back exactly like a
manual reject. Only `pending` leads decay — a promoted/deferred lead is never
touched.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from auto_search import priority

logger = logging.getLogger(__name__)

AUTO_REJECT_REASON = "auto: no new signal — aged out of review"


def watch_ttl_days() -> int:
    try:
        return max(1, int(os.getenv("DISCOVERY_WATCH_TTL_DAYS", "7")))
    except (TypeError, ValueError):
        return 7


def review_ttl_days() -> int:
    try:
        return max(1, int(os.getenv("DISCOVERY_REVIEW_TTL_DAYS", "7")))
    except (TypeError, ValueError):
        return 7


@dataclass
class SweepResult:
    demoted: int = 0                       # watch -> needs_review
    rejected: int = 0                      # needs_review -> rejected
    demoted_keys: list[str] = field(default_factory=list)
    rejected_keys: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"demoted": self.demoted, "rejected": self.rejected,
                "demoted_keys": self.demoted_keys, "rejected_keys": self.rejected_keys}


def _signals_for_intent(row: dict) -> list[dict]:
    """Map a stored company row's signals into the flat shape priority expects."""
    out: list[dict] = []
    for s in row.get("signals") or []:
        p = s.get("payload") or {}
        out.append({
            "signal_type": s.get("signal_type"),
            "title": p.get("job_title"),
            "role": p.get("role"),
            "tier": p.get("tier"),
            "observed_at": s.get("observed_at"),
        })
    return out


def _is_pending(row: dict) -> bool:
    return (row.get("review_status") or "pending") == "pending"


def sweep(repo, *, now: datetime | None = None) -> SweepResult:
    """Run one decay pass over the discovery repo. Idempotent + safe to re-run."""
    now = now or datetime.now(UTC)
    watch_cut = now - timedelta(days=watch_ttl_days())
    reject_cut = now - timedelta(days=watch_ttl_days() + review_ttl_days())
    res = SweepResult()

    # Watch -> Needs review: a still-pending qualified lead that's gone cold.
    for row in repo.panel(statuses=("qualified",)):
        if not _is_pending(row):
            continue
        sigs = _signals_for_intent(row)
        if priority.intent(sigs, now=now).tier == "hot":
            continue                                  # hot stays — it's in-market
        last = priority.last_signal_at(sigs)
        if last is None or last >= watch_cut:
            continue                                  # still fresh — give it time
        key = row.get("normalized_name")
        if key and repo.enter_needs_review(key) is not None:
            res.demoted += 1
            res.demoted_keys.append(key)

    # Needs review -> auto-rejected: no human action AND no fresh signal.
    for row in repo.panel(statuses=("needs_review",)):
        if not _is_pending(row):
            continue
        sigs = _signals_for_intent(row)
        if priority.intent(sigs, now=now).tier == "hot":
            continue                                  # a new signal reheated it — keep
        last = priority.last_signal_at(sigs)
        if last is None or last >= reject_cut:
            continue
        key = row.get("normalized_name")
        if key and repo.set_review(key, "rejected", reason=AUTO_REJECT_REASON) is not None:
            res.rejected += 1
            res.rejected_keys.append(key)

    logger.info("lifecycle sweep: %d watch->review, %d review->auto-rejected",
                res.demoted, res.rejected)
    return res
