"""Buying-intent score for a discovery lead — pure, deterministic, no LLM.

ICP-qualified is not the same as ready-to-buy. A single junior revenue-cycle
posting passes ICP but is weak intent; a new CFO, a revenue-cycle *leader* hire,
several open roles, or an exec engaging are strong. This scores the signal MIX a
company shows (0-100) so Discovery can rank by intent and auto-score only the Hot
tier — the rest are watched until they heat up (see lifecycle.py).

    score = strongest signal's base
          + stacking      (each extra open role, capped)
          + multi-type    (two or more distinct signal types)
          + abm           (on the target list)
          + recency       (a signal in the last week)
    tier  = "hot" if score >= INTENT_HOT (default 65) else "watch"

Every component appends a short human reason, so a rank is never a black box.
Signals are passed as plain dicts ({signal_type, title, role, tier, observed_at})
so both the panel API and the lifecycle sweep can call it from their own shapes.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime

# Hiring one of these is a buying signal (they scope/own the work), not backfill.
_LEADER_RE = re.compile(
    r"\b(chief|c[a-z]o|cxo|vp|svp|evp|vice[\s-]?president|director|head\s+of"
    r"|administrator|executive)\b",
    re.IGNORECASE,
)

# Base weight of the single strongest NON-job signal a company shows. Tuned so a
# clear trigger (new exec) or an engaged exec clears the Hot bar on its own, while
# a layoff/event needs corroboration.
_BASE: dict[str, int] = {
    "leadership_change": 65,   # a new exec is a fresh buying window — hot alone
    "social_engagement": 60,   # an exec engaged with Magical (+recency → hot)
    "layoff": 50,              # cost pressure → RCM-efficiency play; needs more
    "acquisition": 50,         # integration → ops scaling
    "funding_round": 50,
    "event_attendance": 45,
}
_JOB_LEADER = 50               # hiring a revenue-cycle leader (Dir/VP/Mgr/Chief)
_JOB_CORE = 30                 # a core RCM IC role (denials, auth, AR, eligibility…)
_JOB_STANDARD = 18             # a standard IC role (biller, coder, scheduler…)

# A real build-out (3+ open RCM roles) should clear Hot; one or two should not.
_STACK_PER_ROLE = 15
_STACK_CAP = 45
_MULTI_TYPE_BONUS = 20
_ABM_BONUS = 20
_RECENCY_BONUS = 5
_RECENCY_DAYS = 7


def hot_threshold() -> int:
    try:
        return int(os.getenv("DISCOVERY_INTENT_HOT", "65"))
    except (TypeError, ValueError):
        return 65


@dataclass(frozen=True)
class Intent:
    score: int                 # 0-100
    tier: str                  # "hot" | "watch"
    reason: str                # short, human-readable ("New exec · 4 roles open · ABM")


def _job_points(sig: dict) -> tuple[int, str]:
    title = str(sig.get("title") or "")
    if _LEADER_RE.search(title):
        return _JOB_LEADER, "revenue-cycle leader hire"
    tier = str(sig.get("tier") or "").strip().lower()
    if tier == "standard":
        return _JOB_STANDARD, "standard RCM role"
    return _JOB_CORE, "core RCM role"     # core OR unknown tier → fail toward core


_HUMAN: dict[str, str] = {
    "leadership_change": "new exec",
    "social_engagement": "exec engaged",
    "layoff": "layoff",
    "acquisition": "acquisition",
    "funding_round": "funding",
    "event_attendance": "event attendee",
}


def _parse_dt(v) -> datetime | None:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def last_signal_at(signals: list[dict]) -> datetime | None:
    """Freshest signal time — the lifecycle decay clock (no new column needed)."""
    times = [t for t in (_parse_dt(s.get("observed_at")) for s in signals) if t]
    return max(times) if times else None


def outcome_adjustment(outcomes: dict | None = None) -> int:
    """Closed-loop boost from downstream outcomes — PLACEHOLDER (returns 0 today).

    The intent score is signal-based: it predicts who LOOKS in-market. The next
    step is to learn from what actually converts. When an outcomes store exists per
    account — its contacts engaged, a meeting was booked, a deal progressed — this
    returns a positive delta so proven-warm accounts (and lookalikes) rank higher.
    Wired into the call site + surfaced in the UI hint now, so building it later is
    a drop-in, not a refactor.

        outcomes = {"engaged": bool, "meeting_booked": bool, "deal_stage": str, ...}
    """
    return 0


def intent(signals: list[dict], *, abm_confirmed: bool = False,
           now: datetime | None = None, outcomes: dict | None = None) -> Intent:
    """Deterministic buying-intent for a company's grouped signals."""
    now = now or datetime.now(UTC)
    if not signals:
        return Intent(0, "watch", "no signals")

    best, best_reason, best_is_job = 0, "", False
    types: set[str] = set()
    job_count = 0
    for s in signals:
        st = s.get("signal_type")
        types.add(st)
        if st == "job_posting":
            job_count += 1
            pts, why, is_job = (*_job_points(s), True)
        else:
            pts, why, is_job = _BASE.get(st, 25), _HUMAN.get(st, str(st or "signal")), False
        if pts > best:
            best, best_reason, best_is_job = pts, why, is_job

    score = best
    if job_count > 1:
        score += min((job_count - 1) * _STACK_PER_ROLE, _STACK_CAP)

    # Reason leads with the build-out when jobs dominate, else the strongest signal.
    if best_is_job and job_count > 1:
        parts = [f"{job_count} RCM roles open"]
    else:
        parts = [best_reason]
        if job_count > 1:
            parts.append(f"{job_count} roles open")
    if len(types) >= 2:
        score += _MULTI_TYPE_BONUS
        parts.append("multi-signal")
    if abm_confirmed:
        score += _ABM_BONUS
        parts.append("ABM target")
    last = last_signal_at(signals)
    if last and (now - last).days < _RECENCY_DAYS:
        score += _RECENCY_BONUS
    score += outcome_adjustment(outcomes)        # closed-loop learning (0 today)

    score = max(0, min(score, 100))
    tier = "hot" if score >= hot_threshold() else "watch"
    return Intent(score, tier, " · ".join(p for p in parts if p))
