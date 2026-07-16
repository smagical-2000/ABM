"""Outreach positive-response -> Slack. SmartLead + HeyReach webhook events in,
one formatted card out to SLACK_OUTREACH_WEBHOOK — POSITIVE responses only.

Split like engagement/notify.py: the `*_to_card` builders are PURE (tolerant
payload dict -> Slack Block Kit payload or None when the event must not be
forwarded); `post_card` does the one bit of I/O. The webhook receivers in
api/app.py are auth-exempt and guarded by OUTREACH_WEBHOOK_SECRET instead.

Positive-only contract:
- SmartLead: forward ONLY when the event carries a positive reply category
  (Interested / Meeting Request / Information Request / Meeting Booked).
  A bare EMAIL_REPLY with no category yet is NOT forwarded — SmartLead fires
  LEAD_CATEGORY_UPDATED once its categorizer runs, and that event is caught.
- HeyReach: forward actual replies (message / InMail). Connection accepts,
  sends, views etc. are never forwarded.
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

POSITIVE_CATEGORIES = {"interested", "meeting request", "information request",
                       "meeting booked", "positive"}

_REPLY_EVENTS_HEYREACH = {"MESSAGE_REPLY_RECEIVED", "EVERY_MESSAGE_REPLY_RECEIVED",
                          "INMAIL_REPLY_RECEIVED"}
_MEETING_CATEGORIES = {"meeting booked"}                      # Sunny 2026-07-15: the 10-pt
# tag is "Meeting Booked" ONLY; Meeting Request is a positive reply, not a meeting.
_POSITIVE_REPLY_CATEGORIES = {"interested", "meeting request", "information request",
                              "positive"}
SMARTLEAD_INBOX = "https://app.smartlead.ai/app/master-inbox"
HEYREACH_INBOX = "https://app.heyreach.io/inbox"


def _dig(d: dict, *keys, default=""):
    """First non-empty value among top-level keys; tolerates missing/None."""
    for k in keys:
        v = d.get(k)
        if isinstance(v, dict):
            v = v.get("name") or v.get("new_name") or v.get("text") or ""
        if v:
            return str(v)
    return default


def _esc(text: str) -> str:
    """Slack mrkdwn escape for lead-controlled strings (names, companies, reply
    bodies come from arbitrary cold-email recipients — unescaped <url|label>
    would render as a legit-looking link in a channel AEs act on)."""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _category(payload: dict) -> str:
    """The reply category however SmartLead spells it in this event."""
    for key in ("reply_category", "lead_category", "category", "new_category",
                "lead_category_name", "new_lead_category"):
        v = payload.get(key)
        if isinstance(v, dict):
            v = v.get("new_name") or v.get("name")
        if v:
            return str(v).strip()
    return ""


def _snippet(text: str, limit: int = 260) -> str:
    text = " ".join((text or "").split())
    return text[: limit - 1] + "…" if len(text) > limit else text


def _card(header: str, lines: list[str], snippet: str = "") -> dict:
    blocks = [{"type": "header",
               "text": {"type": "plain_text", "text": header, "emoji": True}},
              {"type": "section",
               "text": {"type": "mrkdwn", "text": "\n".join(lines)}}]
    if snippet:
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn", "text": f"> {snippet}"}})
    return {"blocks": blocks}


# ── builders (pure) ──────────────────────────────────────────────────────


_CARD_EVENTS_SMARTLEAD = {"EMAIL_REPLY", "LEAD_CATEGORY_UPDATED"}


def smartlead_event_to_card(payload: dict) -> dict | None:
    """SmartLead webhook event -> Slack card, or None when not a positive reply.
    Gated on BOTH the event type and the category: click/open events echo the
    lead's CURRENT category, so an already-Interested lead's (bot) click would
    otherwise post a false "positive reply" card (QA, 2026-07-15)."""
    etype = _dig(payload, "event_type", "eventType").upper()
    if etype not in _CARD_EVENTS_SMARTLEAD:
        return None
    cat = _category(payload)
    if cat.lower() not in POSITIVE_CATEGORIES:
        return None
    name = (" ".join(v for v in (_dig(payload, "first_name", "lead_first_name"),
                                 _dig(payload, "last_name", "lead_last_name")) if v)
            or _dig(payload, "lead_name") or "Someone")
    email = _dig(payload, "lead_email", "sl_lead_email", "to_email", "email")
    company = _dig(payload, "company_name", "lead_company")
    campaign = (_dig(payload, "campaign_name") or _dig(payload, "campaign_id")
                or "unknown campaign")
    reply = payload.get("reply_message") or payload.get("reply_body") or ""
    if isinstance(reply, dict):
        reply = reply.get("text") or reply.get("html") or ""
    lines = [f"*{_esc(name)}*" + (f" · {_esc(company)}" if company else ""),
             f"Category: *{_esc(cat)}*  ·  Campaign: {_esc(campaign)}"]
    if email:
        lines.append(f"Email: {_esc(email)}")
    inbox = _dig(payload, "app_url", "appUrl") or SMARTLEAD_INBOX
    lines.append(f"<{inbox}|Open in SmartLead inbox>")
    return _card("Positive email reply", lines, _esc(_snippet(str(reply))))


def heyreach_event_to_card(payload: dict) -> dict | None:
    """HeyReach webhook event -> Slack card, or None when not a reply event."""
    etype = _dig(payload, "eventType", "event_type", "type").upper()
    if etype not in _REPLY_EVENTS_HEYREACH:
        return None
    lead = payload.get("lead") or {}
    camp = payload.get("campaign") or {}
    name = (" ".join(v for v in (lead.get("firstName", ""), lead.get("lastName", "")) if v)
            or _dig(payload, "leadName") or "Someone")
    company = lead.get("companyName") or ""
    profile = lead.get("profileUrl") or _dig(payload, "profileUrl")
    campaign = camp.get("name") or _dig(payload, "campaignName") or f"campaign {camp.get('id', '?')}"
    msg = payload.get("message") or payload.get("reply") or ""
    if isinstance(msg, dict):
        msg = msg.get("text") or msg.get("body") or ""
    kind = "InMail reply" if etype.startswith("INMAIL") else "LinkedIn reply"
    lines = [f"*{_esc(name)}*" + (f" · {_esc(company)}" if company else ""),
             f"{kind}  ·  Campaign: {_esc(campaign)}"]
    if profile:
        lines.append(f"<{profile}|LinkedIn profile>  ·  <{HEYREACH_INBOX}|Open HeyReach inbox>")
    else:
        lines.append(f"<{HEYREACH_INBOX}|Open HeyReach inbox>")
    return _card("LinkedIn response", lines, _esc(_snippet(str(msg))))


# ── engagement mapping (pure) ────────────────────────────────────────────


def smartlead_event_to_engagement(payload: dict) -> dict | None:
    """SmartLead webhook event -> an outbound engagement touch, or None.
    outbound_click (1) / outbound_reply (6, positive-categorized replies only) /
    outbound_meeting_booked (10, category "Meeting Booked" only) — the receiver
    crosses it to an ABM account and stores it; unmatched leads are dropped
    there, same policy as every other engagement source."""
    etype = _dig(payload, "event_type", "eventType").upper()
    email = _dig(payload, "lead_email", "sl_lead_email", "to_email", "email").lower()
    if not email:
        return None
    cat = _category(payload).lower()
    if etype in ("EMAIL_LINK_CLICK", "EMAIL_LINK_CLICKED", "LINK_CLICKED"):
        kind = "outbound_click"
    elif etype == "LEAD_CATEGORY_UPDATED" and cat in _MEETING_CATEGORIES:
        kind = "outbound_meeting_booked"
    elif etype == "LEAD_CATEGORY_UPDATED" and cat in _POSITIVE_REPLY_CATEGORIES:
        # Sentiment-gated reply heat (Sunny 2026-07-15): a raw EMAIL_REPLY adds
        # nothing — the 6 pts land only when the reply is categorized positive,
        # so a "not interested" reply can never warm an account.
        kind = "outbound_reply"
    else:
        return None
    name = " ".join(v for v in (_dig(payload, "first_name", "lead_first_name"),
                                _dig(payload, "last_name", "lead_last_name")) if v)
    return {"kind": kind, "email": email, "name": name,
            "company": _dig(payload, "company_name", "lead_company"),
            "title": _dig(payload, "lead_title", "title"),
            "campaign": _dig(payload, "campaign_name") or _dig(payload, "campaign_id"),
            "external_id": f"outbound:{kind}:{email}",
            # Prefer the event's own timestamp so a SmartLead webhook RETRIGGER
            # cannot restamp an old touch as today (phantom momentum guard).
            "occurred_at": _dig(payload, "event_timestamp", "time_replied",
                                "time_clicked", "sent_time", "time"),
            "raw": payload}


# ── I/O ──────────────────────────────────────────────────────────────────


def post_card(card: dict, *, webhook: str | None = None,
              http: httpx.Client | None = None) -> bool:
    """POST one card to the outreach Slack webhook. True on 2xx; never raises."""
    hook = webhook or os.getenv("SLACK_OUTREACH_WEBHOOK")
    if not hook or not card:
        return False
    try:
        r = (http.post(hook, json=card, timeout=15) if http is not None
             else httpx.post(hook, json=card, timeout=15))
        ok = 200 <= r.status_code < 300
        if not ok:
            logger.warning("outreach slack post failed: %s %s", r.status_code, r.text[:120])
        return ok
    except Exception as e:  # noqa: BLE001 — a webhook hiccup must never 500 the receiver
        logger.warning("outreach slack post errored: %s", e)
        return False
