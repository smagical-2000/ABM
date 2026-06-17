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

# kind -> label (no emoji — the Slack card stays clean/professional)
_KIND = {
    "high_intent_lead": "High-intent lead",
    "meeting_booked": "Meeting",
    "opportunity": "Opportunity",
    "reply": "Reply",
    "click": "Click",
    "podcast_lead": "Podcast",
    "tradeshow": "Tradeshow",
    "low_intent_lead": "TOFU content",
}


def build_card(account: dict, events: list[dict], *, dms: list[dict] | None = None,
               app_url: str | None = None, sdr: str | None = None,
               test: bool = False) -> dict:
    """Build the Slack message (Block Kit) for an activated account. PURE.

    `dms` are the enriched decision-makers (name/title/email/phone) — the sales
    packet rendered into the card. `sdr` is rendered as PLAIN TEXT (no @-mention →
    no notification). `test` marks the message as a wiring test.
    """
    name = account.get("name") or account.get("account_id") or "Unknown account"
    tier = account.get("tier") or "—"
    score = account.get("score") or 0
    header = f"[TEST] {name} — {tier}" if test else f"{name} — {tier}"

    bits = [f"*Heat:* {score} pts ({tier})"]
    breakdown = _breakdown(events)
    if breakdown:
        bits.append(f"*Signals:* {breakdown}")
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

    dm_lines = _dm_lines(dms)
    if dm_lines:
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn", "text": "*Decision-makers*\n" + dm_lines}})
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


def activate_account(account: dict, events: list[dict], *, dms: list[dict] | None = None,
                     app_url: str | None = None, sdr: str | None = None, test: bool = False,
                     webhook: str | None = None, http: httpx.Client | None = None) -> bool:
    """Build + post the activation card (with enriched decision-makers). Returns
    True if Slack accepted it."""
    return post_card(build_card(account, events, dms=dms, app_url=app_url, sdr=sdr, test=test),
                     webhook=webhook, http=http)


def _dm_lines(dms: list[dict] | None, *, limit: int = 5) -> str:
    """Up to `limit` decision-makers: '• *Jane Doe* — VP Revenue Cycle\\n   jane@x.com · +1…'."""
    out = []
    for p in (dms or [])[:limit]:
        who = p.get("name") or "—"
        title = f" — {p['title']}" if p.get("title") else ""
        ci = " · ".join(x for x in (p.get("email"), p.get("phone")) if x) or "no contact info found"
        out.append(f"• *{who}*{title}\n   {ci}")
    return "\n".join(out)


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
    """Per-kind counts only (no per-touch spam): 'High-intent lead 1 · Click 2 · Reply 1'.
    Events are one-per-contact×kind, so the count is meaningful. Ordered by weight."""
    if not events:
        return ""
    counts = Counter(e.get("kind") for e in events)
    return " · ".join(f"{_KIND.get(k, k or 'Touch')} {n}" for k, n in counts.most_common())
