"""Engagement activation → Slack. Posts a hot-account card to an incoming webhook.

Split like the rest of the engagement code: `build_card` is PURE (account dict +
events -> Slack Block Kit payload, fully testable, no I/O); `post_card` does the one
bit of I/O (httpx POST to the webhook from SLACK_ENGAGEMENT_WEBHOOK).

SDR tagging is intentionally NOT live yet — we render the owner as plain text, never
an encoded <@id> mention, so nobody gets pinged while we tune the format. Flip
`mention_sdr` (with a real Slack user-id map) when the user says go.
"""

from __future__ import annotations

import logging
import os
from collections import Counter

import httpx

logger = logging.getLogger(__name__)

# kind -> (emoji, label) for the touch timeline in the card
_KIND = {
    "high_intent_lead": ("📝", "High-intent lead"),
    "meeting_booked": ("🤝", "Meeting"),
    "opportunity": ("💰", "Opportunity"),
    "reply": ("✉️", "Reply"),
    "click": ("👆", "Click"),
    "podcast_lead": ("🎙️", "Podcast"),
    "tradeshow": ("🎪", "Tradeshow"),
}
_TIER_EMOJI = {"Hot": "🔥", "Warm": "🌤️", "Some": "🌥️", "Lower": "☁️"}


def build_card(account: dict, events: list[dict], *, app_url: str | None = None,
               sdr: str | None = None, test: bool = False) -> dict:
    """Build the Slack message (Block Kit) for an activated account. PURE.

    `sdr` is rendered as PLAIN TEXT (no @-mention → no notification). `test` marks
    the message as a wiring test so it's obviously ignorable in-channel.
    """
    name = account.get("name") or account.get("account_id") or "Unknown account"
    tier = account.get("tier") or "—"
    score = account.get("score") or 0
    emoji = _TIER_EMOJI.get(tier, "🔥")
    header = (f"🧪 [test] {name}" if test
              else f"{emoji} {name} is {tier}")

    bits = [f"*Heat:* {score} pts ({tier})"]
    breakdown = _breakdown(events)
    if breakdown:
        bits.append(f"*Engagement:* {breakdown}")
    cls = _classification(account)
    if cls:
        bits.append(f"*Classification:* {cls}")
    lists = account.get("lists") or []
    if lists:
        bits.append(f"*Lists:* {', '.join(lists)}")
    if account.get("domain"):
        bits.append(f"*Domain:* {account['domain']}")
    if sdr:
        bits.append(f"*SDR:* {sdr}")        # plain text — not a mention

    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": header[:150]}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(bits)}},
    ]

    timeline = _timeline_lines(events)
    if timeline:
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn", "text": "*Recent touches*\n" + timeline}})
    if app_url and app_url.startswith(("http://", "https://")):   # Slack rejects scheme-less URLs
        blocks.append({"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "Open in console"},
             "url": app_url}]})

    fallback = f"{name} is {tier} ({score} pts)"
    return {"text": fallback, "blocks": blocks}


def post_card(payload: dict, *, webhook: str | None = None,
              http: httpx.Client | None = None) -> bool:
    """POST a prebuilt payload to the Slack incoming webhook. Returns True on 2xx.
    Webhook from SLACK_ENGAGEMENT_WEBHOOK unless passed explicitly."""
    hook = webhook or os.getenv("SLACK_ENGAGEMENT_WEBHOOK")
    if not hook:
        logger.warning("SLACK_ENGAGEMENT_WEBHOOK not set — skipping activation post")
        return False
    try:
        resp = (http.post(hook, json=payload) if http is not None
                else httpx.post(hook, json=payload, timeout=15))
        ok = 200 <= resp.status_code < 300
        if not ok:
            logger.warning("slack webhook -> %s: %s", resp.status_code, resp.text[:200])
        return ok
    except Exception:  # noqa: BLE001 — activation must never crash the caller
        logger.exception("slack webhook post failed")
        return False


def activate_account(account: dict, events: list[dict], *, app_url: str | None = None,
                     sdr: str | None = None, test: bool = False,
                     webhook: str | None = None, http: httpx.Client | None = None) -> bool:
    """Build + post the activation card. Returns True if Slack accepted it."""
    return post_card(build_card(account, events, app_url=app_url, sdr=sdr, test=test),
                     webhook=webhook, http=http)


# ── helpers ──────────────────────────────────────────────────────────────


def _classification(account: dict) -> str | None:
    """Human classification: scored framework + fit tier when present, else the ABM
    segment (junk values already suppressed upstream)."""
    parts = []
    fw = account.get("framework")
    if fw:
        parts.append({"specialty": "Specialty", "health_system": "Health System",
                      "payer": "Payer"}.get(fw, fw))
    fit = account.get("fit_tier") or account.get("tier_label")
    if fit:
        parts.append(str(fit))
    seg = account.get("segment")
    if seg and str(seg) not in _JUNK_SEGMENTS and not parts:
        parts.append(str(seg))
    return " · ".join(parts) if parts else None


# ABM-import artifacts (sheet/tab names) — never show these as a segment. The API
# already cleans segments before the card is built; this is a defensive backstop.
_JUNK_SEGMENTS = frozenset({"Matches", "Sheet30"})


def _breakdown(events: list[dict]) -> str:
    """'5 touches — 2 high-intent leads · 1 podcast · 1 click · 1 reply' from the
    per-kind event counts (events are one-per-contact×kind, so this is meaningful)."""
    if not events:
        return ""
    counts = Counter(e.get("kind") for e in events)
    total = sum(counts.values())
    parts = []
    for kind, n in counts.most_common():
        label = _KIND.get(kind, ("", kind or "touch"))[1].lower()
        parts.append(f"{n} {label}{'s' if n != 1 else ''}")
    return f"{total} touch{'es' if total != 1 else ''} — " + " · ".join(parts)


def _timeline_lines(events: list[dict], *, limit: int = 6) -> str:
    """Up to `limit` most-recent touches, newest first: '📝 High-intent lead · Jun 8 · +10'."""
    rows = sorted(events, key=lambda e: e.get("occurred_at") or "", reverse=True)[:limit]
    out = []
    for e in rows:
        emoji, label = _KIND.get(e.get("kind"), ("•", e.get("kind") or "Touch"))
        day = (e.get("occurred_at") or "")[:10]
        out.append(f"{emoji} {label} · {day} · +{e.get('points', 0)}")
    return "\n".join(out)
