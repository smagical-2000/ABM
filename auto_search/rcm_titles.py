"""Canonical RCM job-title taxonomy — the single source of truth for which
revenue-cycle roles we search and whether each is CORE or STANDARD intent.

Pure data, no I/O and no heavy deps, so BOTH the jobs connector (which scrapes
these titles) and the intent scorer (which weights core vs standard) can share it
without importing each other — the connector pulls in Apify, which `priority`
must not.

  • "core"     — the high-intent RCM work Magical automates directly (prior auth,
                 denials/appeals, eligibility, claims, revenue cycle/integrity,
                 utilization review). ONE posting is a buying signal on its own.
  • "standard" — higher-volume / noisier adjacent roles (billers, coders, patient
                 access, scheduling…). A lone posting is often routine backfill,
                 so the company qualifies only once it STACKS (≥2) — see
                 job_stacking.py. A single-standard company is parked and watched.

TIER shapes WHEN we spend the (expensive) company qualifier and HOW MUCH buying
intent a posting carries (priority.py). It does NOT change what's scraped; every
posting is still pulled and stored. The role bucket is what the UI groups on
("3 Coder jobs"); quotes give an exact-phrase title match.
"""

from __future__ import annotations

from typing import NamedTuple


class EssentialTitle(NamedTuple):
    query: str          # quoted exact-phrase board query
    role: str           # UI grouping bucket
    strength: float     # base signal strength (0–1)
    tier: str           # CORE | STANDARD


CORE, STANDARD = "core", "standard"

ESSENTIAL_RCM_TITLES: list[EssentialTitle] = [
    # ── CORE (11) — a single posting qualifies the company ───────────────
    EssentialTitle('"prior authorization specialist"', "Prior Auth", 0.88, CORE),
    EssentialTitle('"authorization coordinator"', "Prior Auth", 0.84, CORE),
    EssentialTitle('"insurance verification specialist"', "Eligibility", 0.85, CORE),
    EssentialTitle('"eligibility specialist"', "Eligibility", 0.82, CORE),
    EssentialTitle('"claims specialist"', "Claims", 0.82, CORE),
    EssentialTitle('"claims processor"', "Claims", 0.80, CORE),
    EssentialTitle('"denials specialist"', "Denials", 0.86, CORE),
    EssentialTitle('"appeals specialist"', "Appeals", 0.84, CORE),
    EssentialTitle('"revenue cycle specialist"', "Revenue Cycle", 0.82, CORE),
    EssentialTitle('"revenue integrity specialist"', "Revenue Integrity", 0.80, CORE),
    EssentialTitle('"utilization management nurse"', "Utilization Mgmt", 0.78, CORE),
    # ── STANDARD (13) — must STACK (≥2 postings) to spend the qualifier ──
    EssentialTitle('"medical biller"', "Biller", 0.72, STANDARD),
    EssentialTitle('"billing specialist"', "Biller", 0.66, STANDARD),
    EssentialTitle('"medical coder"', "Coder", 0.72, STANDARD),
    EssentialTitle('"cdi specialist"', "CDI", 0.70, STANDARD),
    EssentialTitle('"collections specialist"', "AR / Collections", 0.66, STANDARD),
    EssentialTitle('"payment posting specialist"', "Payment Posting", 0.66, STANDARD),
    EssentialTitle('"patient access representative"', "Patient Access", 0.68, STANDARD),
    EssentialTitle('"referral coordinator"', "Patient Access", 0.64, STANDARD),
    EssentialTitle('"intake coordinator"', "Patient Access", 0.62, STANDARD),
    EssentialTitle('"scheduling coordinator"', "Scheduling", 0.60, STANDARD),
    EssentialTitle('"care coordinator"', "Care Coordination", 0.58, STANDARD),
    EssentialTitle('"patient navigator"', "Care Coordination", 0.58, STANDARD),
    EssentialTitle('"clinical reviewer"', "Clinical Review", 0.60, STANDARD),
]

# Role bucket (lowercased) → canonical tier. Role buckets never span tiers, so
# this is well-defined.
_ROLE_TIER: dict[str, str] = {t.role.lower(): t.tier for t in ESSENTIAL_RCM_TITLES}


def tier_for_role(role: str | None) -> str:
    """Canonical tier (``core``|``standard``) for a stored signal's role bucket.

    Used to recover the tier of a legacy job signal stored before the connector
    persisted it. An unknown role → CORE (fail open), matching the connector's
    own missing-tier stance, so this only ever *demotes* a recognised standard
    role and never silently down-weights an unrecognised one.
    """
    return _ROLE_TIER.get(str(role or "").strip().lower(), CORE)
