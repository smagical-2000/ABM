"""Weekly engagement digest — a deliberately LEAN Slack post of who heated up.

Galyna's rule: reps won't use a wall of data. So this is intentionally minimal —
who moved, one reason, that's it. No rates, no contact counts, no per-touch spam,
no sparklines. One ranked message with a link to the console for the full picture.

Pure builders (`select_movers` + `build_digest`); the one I/O hop (posting) reuses
`notify.post_card`. Safe by design: reads + posts a summary, never enriches, never
spends credits.
"""

from __future__ import annotations

from auto_search.engagement import scoring

# kind -> the single short reason we show (most-actionable phrasing). One per account.
_REASON = {
    "sales_accepted_opportunity": "Sales accepted opp",
    "meeting_booked": "Meeting booked",
    "high_intent_lead": "High-intent lead",
    "tradeshow": "Tradeshow",
    "opportunity": "Opportunity",
    "reply": "Replied",
    "podcast_lead": "Podcast",
    "low_intent_lead": "TOFU content",
    "click": "Email activity",
}

# Low-signal touches that don't, on their own, make an account "heated up" worth a
# rep's attention. An account needs a touch ABOVE this bar in the window to be a mover
# (Galyna: only surface meaningful movement, not "they opened an email").
_NOISE_KINDS = frozenset({"click", "low_intent_lead"})


def select_movers(window_events: list[dict], scores_by_account: dict[str, int]) -> list[dict]:
    """Accounts with MEANINGFUL movement in the window, ranked, each with ONE reason. PURE.

    `window_events` are the engagement events already filtered to the digest window
    (e.g. last 7 days). An account qualifies only if it had a touch above the noise bar
    (a reply / podcast / meeting / lead / SAO — not just a click). `scores_by_account`
    is each account's lifetime heat (for the tier + the 'crossed into a hotter tier this
    week' flag). Tier upgrades rank first, then by points gained. Name comes from the
    event's company (no app-layer join)."""
    by: dict[str, dict] = {}
    for e in window_events:
        aid = e.get("account_id")
        if not aid:
            continue
        m = by.setdefault(aid, {"gained": 0, "company": None, "_best_pts": -1,
                                "_best_kind": None, "meaningful": False})
        pts = int(e.get("points") or 0)
        kind = e.get("kind")
        m["gained"] += pts
        m["company"] = m["company"] or e.get("company")
        if kind not in _NOISE_KINDS:
            m["meaningful"] = True
        if pts > m["_best_pts"]:
            m["_best_pts"], m["_best_kind"] = pts, kind

    movers: list[dict] = []
    for aid, m in by.items():
        if m["gained"] <= 0 or not m["meaningful"]:
            continue
        score = int(scores_by_account.get(aid, m["gained"]))
        tier = scoring.tier_for(score)
        upgraded = tier != scoring.tier_for(score - m["gained"])   # crossed a tier this week
        movers.append({
            "account_id": aid, "name": m["company"] or aid, "tier": tier,
            "score": score, "gained": m["gained"], "upgraded": upgraded,
            "reason": _REASON.get(m["_best_kind"], "Heating up"),
        })
    movers.sort(key=lambda x: (x["upgraded"], x["gained"], x["score"]), reverse=True)
    return movers


def build_digest(movers: list[dict], *, limit: int = 5, console_url: str | None = None,
                 test: bool = False) -> dict:
    """Lean Slack Block Kit payload: a count + the top `limit` movers, one line each,
    '+N more' for the rest, and a console link. PURE. No emoji, no stats dump."""
    total = len(movers)
    top = movers[:limit]
    title = ("[TEST] " if test else "") + "Hot movers this week"

    if not movers:
        body = "No accounts heated up this week."
    else:
        plural = "s" if total != 1 else ""
        rows = []
        for m in top:
            tag = m["tier"] + (" · new" if m["upgraded"] else "")   # 'new' = crossed a tier
            rows.append(f"• *{m['name']}* — {tag} · {m['reason']}")
        body = f"*{total} account{plural} heated up this week.*\n\n" + "\n".join(rows)
        if total > len(top):
            body += f"\n\n+{total - len(top)} more in the console"

    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": title[:150]}},
        {"type": "section", "text": {"type": "mrkdwn", "text": body}},
    ]
    if console_url and console_url.startswith(("http://", "https://")):
        blocks.append({"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "Open console"},
             "url": console_url}]})
    return {"text": f"{total} account{'s' if total != 1 else ''} heated up this week",
            "blocks": blocks}
