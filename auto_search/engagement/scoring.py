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
    "meeting_booked": 10,   # meeting agreed / booked
    "podcast_lead": 4,      # podcast listen/download lead (ICP Yes/Maybe)
    "opportunity": 10,      # SFDC open/won opportunity (≈ BOFU — active deal)
    "high_intent_lead": 10, # SFDC high-intent inbound lead (contact/sales form ≈ BOFU)
}

# Touches we record for rates + the timeline but that score zero — kept explicit
# (not silently absent) so the intent is obvious to a reviewer.
ZERO_POINT_KINDS = frozenset({"delivered", "open", "bounce"})

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
