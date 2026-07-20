"""Engagement heat scorer — PURE, deterministic, no I/O (mirrors priority.py).

The single place the engagement weights + tiers live, so they stay auditable and
testable. A touch `kind` maps to points; an account's heat is the sum of its touch
points (the repo enforces one touch per contact x kind, so a long contact list
cannot inflate). Canonical rules: docs/engagement/PRD.md (the "Account Scoring
Rules" sheet, email subset for the Reply.io phase).

A new channel/kind is added by one row in POINTS — nothing else changes.
"""

from __future__ import annotations

from dataclasses import dataclass

# kind -> points. A subset of the canonical cross-channel scoring matrix, grown
# one row at a time as each source comes online (see docs/engagement/PRD.md).
POINTS: dict[str, int] = {
    "click": 1,             # email click
    "reply": 6,             # email reply  (≈ TOFU lead)
    "meeting_booked": 10,   # SFDC booked meeting (Event Type=Meeting) — agreed/booked
    "podcast_lead": 4,      # podcast listen/download lead (ICP Yes/Maybe)
    "opportunity": 10,      # SFDC open/won opportunity (NOT captured today — see sync.py)
    "high_intent_lead": 10, # SFDC high-intent inbound lead (contact/sales form = BOFU)
    "tradeshow": 10,        # SFDC tradeshow lead that booked a meeting (Status=Qualified)
    "low_intent_lead": 6,   # SFDC TOFU lead — filled in a TOFU form (LeadSource '… | TOFU')
    "linkedin_tofu": 6,     # reaction on a Magical TOFU LinkedIn ad → Airtable (TOFU engagement)
    "linkedin_reply": 6,    # replied to our HeyReach outreach DM (≈ email reply weight)
    "linkedin_connect_message": 10,  # accepted a connection request that carried our
    #                         personalized note — a warm, intent-bearing accept (MAR2)
    "linkedin_connect": 2,  # accepted a BARE connection request (matrix: LinkedIn_Connect 2)
    # Outbound (SmartLead) — same canonical weights as their inbound twins, but
    # distinct kinds so the tracker timeline reads "outbound ..." explicitly.
    "outbound_click": 1,           # clicked a link in an outbound SmartLead email
    "outbound_reply": 6,           # POSITIVE-categorized reply to an outbound email
    "outbound_meeting_booked": 10, # SmartLead category "Meeting Booked"
}

# Touches we record for rates + the timeline but that score zero — kept explicit
# (not silently absent) so the intent is obvious to a reviewer.
ZERO_POINT_KINDS = frozenset({"delivered", "open", "bounce"})

# Retired signal kinds: events may still exist in storage (historical, kept for
# audit) but are EXCLUDED from heat, breakdowns, momentum, and the timeline
# everywhere they're read. SAO was replaced by `meeting_booked` in the 2026-06
# review, so it must no longer count or display. (The SQL view in
# engagement_schema.sql hardcodes the same literal — keep them in sync.)
DEPRECATED_KINDS = frozenset({"sales_accepted_opportunity"})

# ── click cap (Sunny 2026-07-20, AGT-1453) ───────────────────────────────────
# Corporate email-security scanners auto-click every link in an email (216
# clicks in 4 days, 80+ inside a 7-minute burst), so raw click volume is
# bot-dominated and uncapped clicks silently inflate accounts to fake Hot.
# Rule: click-kind events contribute AT MOST `CLICK_CAP` points per account —
# a 37-click storm scores exactly like 3 real clicks. Events stay stored in
# full (audit trail + rates); the cap applies wherever heat is AGGREGATED. The
# engaged_accounts SQL view, the JSON rollup, scores_before, the audit
# recompute, and the drawer's tier-journey strip must stay in lockstep with
# `capped_score` (the SQL twin lives in engagement_schema.sql).
CLICK_KINDS = frozenset({"click", "outbound_click", "email_click"})
CLICK_CAP = 3


def capped_score(total: int, click_points: int) -> int:
    """Account heat with the click cap applied: click points beyond CLICK_CAP
    are subtracted from the raw sum. PURE."""
    return int(total) - max(int(click_points) - CLICK_CAP, 0)


# Heat tier thresholds (inclusive lower bound), highest first.
_TIERS: tuple[tuple[int, str], ...] = ((21, "Hot"), (12, "Warm"), (6, "Some"), (0, "Lower"))


def points_for(kind: str, channel: str | None = None) -> int:
    """Points for a touch `kind`. `channel` is reserved for future per-channel
    rules (e.g. a LinkedIn reply weighted differently than an email reply)."""
    return POINTS.get((kind or "").strip().lower(), 0)


def tier_for(score: int) -> str:
    """Heat tier for a total score: Lower 0-5 · Some 6-11 · Warm 12-20 · Hot 21+."""
    for low, label in _TIERS:
        if score >= low:
            return label
    return "Lower"


@dataclass(frozen=True)
class Heat:
    score: int
    tier: str


def heat(score: int) -> Heat:
    s = max(0, int(score))
    return Heat(s, tier_for(s))
