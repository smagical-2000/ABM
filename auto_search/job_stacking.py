"""Per-company stacking gate for job-posting discovery — cost shaping.

The jobs connector emits one signal per open RCM req, each tagged with a TIER
(see job_postings.ESSENTIAL_RCM_TITLES):

  • CORE roles (prior auth, denials/appeals, eligibility, claims, revenue
    cycle/integrity, utilization review) are the high-intent work Magical
    automates directly — a single open req is a buying signal on its own.
  • STANDARD roles (billers, coders, patient access, scheduling…) are
    higher-volume and noisier — one open req is often routine backfill.

This module decides, per company, whether its CURRENT signals justify spending
the (expensive, web-search) company qualifier now, or whether to PARK the
company and watch it until it stacks. The rule:

    QUALIFY if  ANY non-job signal           (the jobs gate must never suppress
                                               leadership / funding / social…)
            OR  ≥1 CORE job posting
            OR  ≥STACK_MIN STANDARD postings  (a real revenue-cycle build-out)
    PARK    if  a single STANDARD posting and nothing above

Parked companies cost nothing to qualify; they're re-evaluated every run, so a
company that opens a second RCM req auto-qualifies on the next pass. Because the
jobs connector pulls a wide "currently-open" window (see discovery_runner), the
co-open reqs land in the same run and stack within ONE decision — no cross-run
state is needed here for correctness (the parked store is only a watch ledger).

Pure + deterministic: a function of the passed signals only. It reads each job
signal's payload["tier"]; a missing/unknown tier FAILS OPEN (treated as core →
qualify), so a connector change can never silently start parking real signals —
the same fail-open stance as the job qualifier.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from auto_search.models import RawSignal

# Distinct STANDARD postings a company must have open at once before we spend
# the company qualifier on it. 2 = "more than one routine backfill" = a
# deliberate revenue-cycle build-out. Env-tunable, floored at 2.
STACK_MIN_STANDARD = max(2, int(os.getenv("DISCOVERY_JOBS_STACK_MIN", "2")))

# How long a parked company stays on the watch list without being seen again
# before it's pruned (it stopped hiring, or we stopped searching that title).
# The repos use this to keep the watch list self-correcting.
PARK_TTL_DAYS = max(1, int(os.getenv("DISCOVERY_JOBS_PARK_TTL_DAYS", "30")))

_JOB = "job_posting"
_STANDARD = "standard"


@dataclass(frozen=True)
class StackDecision:
    """Why a company was qualified or parked — drives logs + the watch UI."""

    action: str                       # "qualify" | "park"
    reason: str                       # short, human-readable
    core_roles: tuple[str, ...]       # distinct CORE role buckets seen
    standard_roles: tuple[str, ...]   # distinct STANDARD role buckets seen
    job_postings: int                 # total deduped job postings in the group
    standard_postings: int            # of those, how many are standard-tier

    @property
    def parked(self) -> bool:
        return self.action == "park"


def stacking_decision(signals: list[RawSignal]) -> StackDecision:
    """Decide qualify-vs-park for one company's grouped signals (pure)."""
    jobs = [s for s in signals if s.signal_type == _JOB]
    has_other = any(s.signal_type != _JOB for s in signals)

    core_roles: list[str] = []
    standard_roles: list[str] = []
    standard_postings = 0
    for s in jobs:
        role = s.payload.get("role") or "RCM"
        tier = (s.payload.get("tier") or "").strip().lower()
        if tier == _STANDARD:
            standard_postings += 1
            if role not in standard_roles:
                standard_roles.append(role)
        # core OR unknown/missing tier → fail open (counts as core, qualifies)
        elif role not in core_roles:
            core_roles.append(role)

    core_t, std_t = tuple(core_roles), tuple(standard_roles)
    base = {
        "core_roles": core_t, "standard_roles": std_t,
        "job_postings": len(jobs), "standard_postings": standard_postings,
    }

    if has_other:
        return StackDecision("qualify", "non-job signal present", **base)
    if core_t:
        return StackDecision("qualify", f"core role: {core_t[0]}", **base)
    if standard_postings >= STACK_MIN_STANDARD:
        label = " + ".join(std_t) if std_t else f"{standard_postings} postings"
        return StackDecision("qualify", f"stacked: {label}", **base)
    if standard_postings == 1:
        only = std_t[0] if std_t else "RCM"
        return StackDecision("park", f"single standard role: {only}", **base)
    # No job postings and no other signal — shouldn't happen for a real group;
    # fail open rather than silently drop.
    return StackDecision("qualify", "no job postings", **base)


def should_park(signals: list[RawSignal]) -> bool:
    """True when the jobs gate parks this company (single standard role only)."""
    return stacking_decision(signals).parked


def watch_record(
    company_key: str, signals: list[RawSignal], decision: StackDecision,
) -> dict:
    """Build the compact 'watch' row persisted for a parked company.

    Display-only provenance for the watch UI — enough to show "X has one open
    <role>" and link to the posting. The qualify decision never reads this back
    (the wide pull window is the memory), so it can stay lean.
    """
    rep = max(
        (s for s in signals if s.signal_type == _JOB),
        key=lambda s: s.signal_strength, default=None,
    )
    p = rep.payload if rep else {}
    return {
        "company_key": company_key,
        "name": rep.company_name_raw if rep else company_key,
        "domain": rep.company_domain_raw if rep else None,
        "role": decision.standard_roles[0] if decision.standard_roles else "RCM",
        "roles": list(decision.standard_roles),
        "postings": decision.standard_postings,
        "state": p.get("state"),
        "city": p.get("city"),
        "sample_url": p.get("job_url"),
        "sample_title": p.get("job_title"),
        "observed_at": rep.observed_at.isoformat() if rep else None,
    }
