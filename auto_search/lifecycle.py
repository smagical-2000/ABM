"""Self-cleaning lead lifecycle — the TTL decay (and re-heat) that keeps Discovery
honest: low-intent leads age out, re-heated ones jump back in.

Reads qualified / needs-review companies (with their signals) from the discovery
repo, recomputes buying intent, and moves each one along the chain:

    Watch         no new signal for WATCH_TTL days          ->  Needs review
    Needs review  re-heated to Hot, AND was qualified        ->  Qualified (back in)
    Needs review  in review > REVIEW_TTL days, still cold     ->  auto-rejected

Two different clocks, on purpose:
  • Watch -> Needs review keys off SIGNAL age (a qualified lead that's gone cold).
  • Needs review -> rejected keys off TIME IN REVIEW (entered_review_at), NOT signal
    age — so a lead with a perpetually-fresh signal still ages out if nobody acts.

Promotion is ORIGIN-aware, which matters: a 'decayed' lead (was qualified, then
cooled) re-heating to Hot is promoted straight back. An 'ingest' lead is in review
because the AI couldn't confidently QUALIFY it — buying intent says it looks
in-market, but that's a different question from "is it even a fit", the one the
queue exists to answer. So an ingest lead is NEVER auto-promoted or auto-scored: it
stays for a human (now surfaced AS Hot, sorted to the top). Hot leads of either
origin are never auto-rejected.

Intent here is computed the SAME way the panel computes it — including the ABM
bonus when an `abm_index` is supplied — so "Hot" means one thing in both places.

The transitions reuse existing state: Watch->review and review->qualified are
icp_status flips, and the auto-reject is review_status='rejected' (icp_status
untouched), so /restore brings an aged-out lead back exactly like a manual reject.
Only `pending` leads move — a promoted/deferred lead is never touched. The caller
(run_discovery / the API sweep) optionally auto-scores the promoted keys.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from auto_search import priority
from auto_search.abm.annotate import match_one, states_from_locations

logger = logging.getLogger(__name__)

AUTO_REJECT_REASON = "auto: no action — aged out of review"


def watch_ttl_days() -> int:
    try:
        return max(1, int(os.getenv("DISCOVERY_WATCH_TTL_DAYS", "7")))
    except (TypeError, ValueError):
        return 7


# Per-signal Watch TTL (days): a signal's useful life depends on its type. A hire
# goes stale fast; a leadership change keeps a buying window open for ~a month; M&A
# / funding / distress signals stay relevant for months. Galyna's review spec
# (2026-06): hiring 7, CXO changes 30, M&A / shutdown 30+. Hiring (and anything
# unlisted) uses the env-tunable base watch_ttl_days() (7); the entries below are
# the longer-lived overrides. A lead stays on Watch until its LONGEST-lived signal
# expires (see _watch_due), so a fresh M&A keeps it even after its hire cooled.
_SIGNAL_TTL_DAYS: dict[str, int] = {
    "leadership_change": 30,   # new exec — buying window stays open ~a month
    "acquisition": 45,         # M&A — integration plays unfold over months
    "funding_round": 45,       # fresh capital — spend window stays open
    "layoff": 45,              # distress / shutdown — RCM-efficiency play persists
}


def ttl_days_for_signal(signal_type: str | None) -> int:
    """Watch TTL for one signal type, in days. Hiring + unlisted types use the
    env-tunable base watch_ttl_days(); leadership / M&A / funding / layoff override."""
    return _SIGNAL_TTL_DAYS.get(signal_type or "", watch_ttl_days())


def review_ttl_days() -> int:
    try:
        return max(1, int(os.getenv("DISCOVERY_REVIEW_TTL_DAYS", "7")))
    except (TypeError, ValueError):
        return 7


@dataclass
class SweepResult:
    demoted: int = 0                       # watch -> needs_review
    promoted: int = 0                      # needs_review -> qualified (re-heated to Hot)
    rejected: int = 0                      # needs_review -> rejected (aged out of review)
    demoted_keys: list[str] = field(default_factory=list)
    promoted_keys: list[str] = field(default_factory=list)
    rejected_keys: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"demoted": self.demoted, "promoted": self.promoted,
                "rejected": self.rejected, "demoted_keys": self.demoted_keys,
                "promoted_keys": self.promoted_keys, "rejected_keys": self.rejected_keys}


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


def _parse_dt(v) -> datetime | None:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def _days_until(due: datetime, now: datetime) -> int:
    """Whole days until `due`, rounded up; 0 once it's due/overdue."""
    secs = (due - now).total_seconds()
    return math.ceil(secs / 86400) if secs > 0 else 0


def _watch_due(signals: list[dict] | None, now: datetime) -> datetime | None:
    """When a Watch lead drops to Needs-review: the LATEST per-signal expiry,
    i.e. max over signals of (observed_at + that signal type's TTL). A lead lives
    until its longest-lived signal goes stale, so a 20-day-old M&A (45d TTL) keeps
    it even after a 10-day-old hire (7d TTL) has cooled. None = no dated signal."""
    due: datetime | None = None
    for s in signals or []:
        seen = _parse_dt(s.get("observed_at"))
        if seen is None:
            continue
        expiry = seen + timedelta(days=ttl_days_for_signal(s.get("signal_type")))
        if due is None or expiry > due:
            due = expiry
    return due


def next_transition(*, icp_status: str | None, tier: str | None,
                    signals: list[dict] | None = None,
                    entered_review_at: str | None = None,
                    now: datetime | None = None) -> tuple[str | None, int | None]:
    """The lead's next AUTOMATIC move, for the panel's TTL badge. Lives here so the
    badge can never drift from sweep() — it mirrors the exact same cutoffs:

        ("review", n)  a qualified Watch lead drops to Needs review in n days
                       (per-signal clock: latest of observed_at + that type's TTL)
        ("reject", n)  a Needs-review lead auto-rejects in n days
                       (time-in-review clock: entered_review_at + REVIEW_TTL)
        (None, None)   Hot (in-market — never decays), or no clock to show

    "in 0d" means the next daily sweep will act. The caller should only ask for
    `pending` leads — a promoted/deferred lead never decays.
    """
    if tier == "hot":
        return None, None                      # in-market — the sweep never moves it
    now = now or datetime.now(UTC)
    if icp_status == "qualified":
        due = _watch_due(signals, now)
        if due is not None:
            return "review", _days_until(due, now)
    if icp_status == "needs_review":
        entered = _parse_dt(entered_review_at)
        if entered is not None:
            return "reject", _days_until(entered + timedelta(days=review_ttl_days()), now)
    return None, None


def _abm_confirmed(abm_index, row: dict) -> bool:
    """Is this row a CONFIRMED ABM target? Mirrors the panel's annotation
    (name/domain + signal-state corroboration) so the sweep's Hot bar matches the
    panel's. No-op (False) when no list is loaded — same as today."""
    if abm_index is None:
        return False
    states = states_from_locations(
        (s.get("payload") or {}).get("location") for s in row.get("signals") or []
    )
    m = match_one(abm_index, name=row.get("display_name") or row.get("normalized_name"),
                  domain=row.get("domain"), states=states)
    return bool(m and m.tier == "confirmed")


def sweep(repo, *, now: datetime | None = None, abm_index=None) -> SweepResult:
    """Run one decay/re-heat pass over the discovery repo. Idempotent + safe to
    re-run. Pass `abm_index` so the sweep's Hot matches the panel's Hot."""
    now = now or datetime.now(UTC)
    review_cut = now - timedelta(days=review_ttl_days())
    res = SweepResult()

    # Watch -> Needs review: a still-pending qualified lead whose signals went cold.
    # "Cold" is per-signal now: the lead survives until its longest-lived signal
    # expires (hire 7d, leadership 30d, M&A/funding/layoff 45d) — see _watch_due.
    for row in repo.panel(statuses=("qualified",)):
        if not _is_pending(row):
            continue
        sigs = _signals_for_intent(row)
        if priority.intent(sigs, now=now,
                           abm_confirmed=_abm_confirmed(abm_index, row)).tier == "hot":
            continue                                  # hot stays — it's in-market
        due = _watch_due(sigs, now)
        if due is None or due > now:
            continue                                  # still fresh — give it time
        key = row.get("normalized_name")
        if key and repo.enter_needs_review(key) is not None:
            res.demoted += 1
            res.demoted_keys.append(key)

    # Needs review: re-heated to Hot -> promote back; else in review too long -> reject.
    for row in repo.panel(statuses=("needs_review",)):
        if not _is_pending(row):
            continue
        key = row.get("normalized_name")
        if not key:
            continue
        sigs = _signals_for_intent(row)
        if priority.intent(sigs, now=now,
                           abm_confirmed=_abm_confirmed(abm_index, row)).tier == "hot":
            # Re-heated to Hot. Only auto-promote a lead that was ALREADY qualified
            # and merely cooled ('decayed'). An 'ingest' lead is in review because
            # the AI couldn't confidently qualify it — intent doesn't answer that, a
            # human must — so it stays put (surfaced as Hot), never auto-promoted or
            # auto-scored. Either way, a Hot lead is never auto-rejected.
            if row.get("review_origin") == "decayed" and \
                    repo.promote_from_review(key) is not None:
                res.promoted += 1
                res.promoted_keys.append(key)
            continue
        entered = _parse_dt(row.get("entered_review_at"))
        if entered is None or entered > review_cut:
            continue                                  # not in review long enough yet
        if repo.set_review(key, "rejected", reason=AUTO_REJECT_REASON) is not None:
            res.rejected += 1
            res.rejected_keys.append(key)

    logger.info("lifecycle sweep: %d watch->review, %d review->promoted, "
                "%d review->auto-rejected", res.demoted, res.promoted, res.rejected)
    return res
