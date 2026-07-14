"""Outreach dashboard aggregator — SmartLead (email) + HeyReach (LinkedIn).

Shapes the two executors' raw analytics into one dashboard payload. Rules:
- Rates are ALWAYS recomputed from raw counts here (never trusted from the
  APIs) so email and LinkedIn percentages mean the same thing: unique events
  over sends, as a 0-100 float rounded to 1dp, None when the denominator is 0.
- Channels are independent: one side failing (or unconfigured) returns its
  own {configured/error} block and never blanks the other.
- Per-campaign loops are bounded (_MAX_CAMPAIGNS) and fetched concurrently;
  a single campaign's fetch error drops that row, never the whole channel.

SmartLead fields verified live 2026-07-13 (counts arrive as strings);
HeyReach GetOverallStats shape verified the same day.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

_MAX_CAMPAIGNS = 50          # per-channel bound on the per-campaign stats loop
_TREND_DAYS = 30             # byDayStats entries passed through to the UI


def _num(v) -> int:
    """SmartLead counts arrive as strings ('0'); coerce defensively."""
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _rate(n: int, d: int) -> float | None:
    """Percent 0-100 rounded to 1dp; None (not 0.0) when nothing was sent,
    so the UI can render an em-dash instead of a misleading zero."""
    return round(100.0 * n / d, 1) if d else None


# ── email (SmartLead) ────────────────────────────────────────────────────


def shape_email_campaign(raw: dict) -> dict:
    """One SmartLead campaign's analytics -> a dashboard row."""
    sent = _num(raw.get("sent_count"))
    opens = _num(raw.get("unique_open_count"))
    clicks = _num(raw.get("unique_click_count"))
    replies = _num(raw.get("reply_count"))
    bounces = _num(raw.get("bounce_count"))
    lead_stats = raw.get("campaign_lead_stats") or {}
    return {
        "id": raw.get("id"), "name": raw.get("name"), "status": raw.get("status"),
        "leads": _num(lead_stats.get("total")),
        "sent": sent, "opens": opens, "clicks": clicks, "replies": replies,
        "bounces": bounces, "unsubscribes": _num(raw.get("unsubscribed_count")),
        "interested": _num(lead_stats.get("interested")),
        "open_rate": _rate(opens, sent), "click_rate": _rate(clicks, sent),
        "reply_rate": _rate(replies, sent), "bounce_rate": _rate(bounces, sent),
    }


async def collect_email(smartlead) -> dict:
    """SmartLead channel block: per-campaign rows + a summed overall."""
    if smartlead is None:
        return {"configured": False}
    try:
        campaigns = (await smartlead.list_campaigns())[:_MAX_CAMPAIGNS]
        raws = await asyncio.gather(
            *(smartlead.campaign_analytics(c["id"]) for c in campaigns),
            return_exceptions=True)
    except Exception as e:  # noqa: BLE001 — channel-level failure is a payload state
        logger.warning("outreach: smartlead fetch failed: %s", e)
        return {"configured": True, "error": str(e)}
    rows, dropped = [], 0
    for c, raw in zip(campaigns, raws, strict=True):
        if isinstance(raw, Exception):
            dropped += 1
            logger.warning("outreach: smartlead campaign %s failed: %s", c["id"], raw)
            continue
        rows.append(shape_email_campaign(raw))
    totals = {k: sum(r[k] for r in rows) for k in
              ("leads", "sent", "opens", "clicks", "replies", "bounces",
               "unsubscribes", "interested")}
    overall = {**totals,
               "open_rate": _rate(totals["opens"], totals["sent"]),
               "click_rate": _rate(totals["clicks"], totals["sent"]),
               "reply_rate": _rate(totals["replies"], totals["sent"]),
               "bounce_rate": _rate(totals["bounces"], totals["sent"])}
    rows.sort(key=lambda r: (r["sent"], r["leads"]), reverse=True)
    return {"configured": True, "overall": overall, "campaigns": rows,
            "campaigns_errored": dropped}


# ── linkedin (HeyReach) ──────────────────────────────────────────────────


def shape_linkedin_stats(raw: dict) -> dict:
    """HeyReach overallStats -> dashboard counters with recomputed rates."""
    s = raw.get("overallStats") or {}
    conn_sent = _num(s.get("connectionsSent"))
    conn_accepted = _num(s.get("connectionsAccepted"))
    msgs = _num(s.get("messagesSent"))
    msg_replies = _num(s.get("totalMessageReplies"))
    inmails = _num(s.get("inmailMessagesSent"))
    inmail_replies = _num(s.get("totalInmailReplies"))
    return {
        "connections_sent": conn_sent, "connections_accepted": conn_accepted,
        "accept_rate": _rate(conn_accepted, conn_sent),
        "messages_sent": msgs, "message_replies": msg_replies,
        "message_reply_rate": _rate(msg_replies, msgs),
        "inmails_sent": inmails, "inmail_replies": inmail_replies,
        "inmail_reply_rate": _rate(inmail_replies, inmails),
        "profile_views": _num(s.get("profileViews")),
        "leads_contacted": _num(s.get("uniqueLeadsContacted")),
        "interested": _num(s.get("autoTaggedInterested")),
    }


def shape_linkedin_trend(raw: dict) -> list[dict]:
    """byDayStats map -> a sorted, bounded [{date, ...counters}] series."""
    by_day = raw.get("byDayStats") or {}
    days = sorted(by_day.keys())[-_TREND_DAYS:]
    return [{"date": d, **{k: _num(v) for k, v in (by_day[d] or {}).items()}}
            for d in days]


async def collect_linkedin(heyreach) -> dict:
    """HeyReach channel block: overall + trend + per-campaign rows."""
    if heyreach is None:
        return {"configured": False}
    try:
        overall_raw = await heyreach.overall_stats()
        campaigns = (await heyreach.list_campaigns(limit=_MAX_CAMPAIGNS))[:_MAX_CAMPAIGNS]
        raws = await asyncio.gather(
            *(heyreach.overall_stats(campaign_ids=[c["id"]]) for c in campaigns),
            return_exceptions=True)
    except Exception as e:  # noqa: BLE001 — channel-level failure is a payload state
        logger.warning("outreach: heyreach fetch failed: %s", e)
        return {"configured": True, "error": str(e)}
    rows, dropped = [], 0
    for c, raw in zip(campaigns, raws, strict=True):
        if isinstance(raw, Exception):
            dropped += 1
            logger.warning("outreach: heyreach campaign %s failed: %s", c["id"], raw)
            continue
        rows.append({"id": c["id"], "name": c["name"], "status": c["status"],
                     **shape_linkedin_stats(raw)})
    rows.sort(key=lambda r: (r["connections_sent"], r["messages_sent"]), reverse=True)
    return {"configured": True, "overall": shape_linkedin_stats(overall_raw),
            "trend": shape_linkedin_trend(overall_raw),
            "campaigns": rows, "campaigns_errored": dropped}


# ── the payload ──────────────────────────────────────────────────────────


async def collect(*, smartlead, heyreach) -> dict:
    """The full dashboard payload; channels fetched concurrently, isolated."""
    email, linkedin = await asyncio.gather(collect_email(smartlead),
                                           collect_linkedin(heyreach))
    return {"fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "email": email, "linkedin": linkedin}
