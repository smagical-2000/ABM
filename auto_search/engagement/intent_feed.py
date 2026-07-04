"""First-party engagement -> intent signals for the FIT scorer.

The fit scorer's intent dimension researches PUBLIC signals (hiring, press,
leadership). For small private practices there often are none — while our own
engagement store may hold the strongest intent that exists (a booked meeting,
a BOFU form, ad engagement). This module turns an account's engagement events
into the same signal dicts the engine already carries from Discovery
(`_signals_block` -> "weight toward the intent dimension"), so a score can
never again claim "no signals found" about a company that booked a meeting
with us. Pure: events in, signal dicts out.
"""

from __future__ import annotations

from auto_search.engagement.scoring import DEPRECATED_KINDS, POINTS

# Scorer-facing phrasing per kind (board-facing: factual, no hype).
_LABELS: dict[str, str] = {
    "meeting_booked": "Booked a meeting with our team (SFDC)",
    "high_intent_lead": "High-intent inbound lead — contact/sales form (SFDC)",
    "tradeshow": "Tradeshow lead that booked a meeting (SFDC)",
    "opportunity": "Open/won opportunity (SFDC)",
    "reply": "Replied to our outbound email",
    "low_intent_lead": "Filled a TOFU form (SFDC lead)",
    "linkedin_tofu": "Engaged with a Magical LinkedIn ad",
    "podcast_lead": "Podcast listener lead",
    "click": "Clicked an outbound email",
}


def to_intent_signals(events: list[dict], *, limit: int = 6) -> list[dict]:
    """Collapse engagement events into scorer signals: one per kind, counted
    ("x3") and dated by the LATEST occurrence, strongest kinds first. Zero-point
    touches (delivered/open/bounce) and deprecated kinds never appear."""
    per_kind: dict[str, dict] = {}
    for e in events or []:
        kind = e.get("kind") or ""
        if kind not in POINTS or kind in DEPRECATED_KINDS:
            continue
        when = (e.get("occurred_at") or "")[:10]
        slot = per_kind.setdefault(kind, {"count": 0, "latest": ""})
        slot["count"] += 1
        slot["latest"] = max(slot["latest"], when)

    ranked = sorted(per_kind.items(),
                    key=lambda kv: (POINTS.get(kv[0], 0), kv[1]["latest"]),
                    reverse=True)   # strongest kind first, then most recent
    out = []
    for kind, slot in ranked[:max(0, limit)]:
        label = _LABELS.get(kind, kind.replace("_", " "))
        times = f" x{slot['count']}" if slot["count"] > 1 else ""
        when = f", latest {slot['latest']}" if slot["latest"] else ""
        out.append({
            "signal_type": f"engagement_{kind}",
            "summary": f"{label}{times}{when} — first-party engagement from our own funnel",
            "url": None,
        })
    return out
