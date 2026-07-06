"""Cross-channel stop rules — reply anywhere, pause everywhere (Phase 3).

The orchestration payoff: when a lead REPLIES on one channel, the other
channels' outreach for that account is paused so we never double-touch a hot
conversation. Locked v1 rule:

    email reply    (Reply.io, landed by the Phase 2 sync)  -> stop the account's
                   HeyReach leads (StopLeadInCampaign per enrolled profile)
    linkedin reply (HeyReach webhook -> engagement event)  -> remove the account's
                   enrolled contacts from their Reply.io campaign (best-effort)

Design, mirroring the rest of the module:
  • Idempotent: `campaign_stops` UNIQUE(account, channel, reason) is CLAIMED
    before any tool call — a re-sweep can never re-fire the same stop.
  • Best-effort per action: one tool failure is logged and counted, never raised.
  • Cursor: `campaigns_stop_cursor` (engagement settings) — each sweep only
    looks at replies newer than the last one, so the sweep is O(new replies).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

CURSOR_KEY = "campaigns_stop_cursor"
_REPLY_KINDS = {"reply": "email", "linkedin_reply": "linkedin"}


def replies_since(events: list[dict], cursor: str) -> list[dict]:
    """Reply events newer than the cursor, oldest first (pure, testable)."""
    out = [e for e in events
           if e.get("kind") in _REPLY_KINDS and e.get("account_id")
           and str(e.get("occurred_at") or "") > cursor]
    out.sort(key=lambda e: str(e.get("occurred_at") or ""))
    return out


async def sweep(*, campaign_repo, engagement_repo,
                heyreach_client=None, replyio_client=None) -> dict:
    """One stop-rule pass. Returns {checked, stopped_linkedin, stopped_email, ...}."""
    cursor = engagement_repo.get_setting(CURSOR_KEY) or ""
    new = replies_since(engagement_repo.recent_events(limit=500), cursor)
    stats = {"checked": len(new), "stopped_linkedin": 0, "stopped_email": 0,
             "stop_failures": 0}
    latest = cursor
    for ev in new:
        latest = max(latest, str(ev.get("occurred_at") or ""))
        source = _REPLY_KINDS[ev["kind"]]
        account_id = ev["account_id"]
        rows = [r for r in campaign_repo.enrollments(account_id=account_id)
                if r.get("status") == "enrolled"]

        if source == "email" and heyreach_client is not None:
            # Stop every enrolled HeyReach lead for this account, once.
            li_rows = [r for r in rows if r.get("channel") == "linkedin"]
            if li_rows and campaign_repo.add_stop({
                    "account_id": account_id, "channel": "linkedin",
                    "reason": "reply:email", "detail": {"event": ev.get("external_id")}}):
                for r in li_rows:
                    url = (r.get("detail") or {}).get("profile_url") \
                        or str(r.get("contact_ext") or "")[3:]
                    ok = await heyreach_client.stop_lead(
                        campaign_id=int(r["campaign_id"]), profile_url=url)
                    stats["stopped_linkedin" if ok else "stop_failures"] += 1

        if source == "linkedin" and replyio_client is not None:
            # Pull the account's enrolled contacts out of their Reply.io campaign.
            em_rows = [r for r in rows if r.get("channel") == "email" and r.get("email")]
            if em_rows and campaign_repo.add_stop({
                    "account_id": account_id, "channel": "email",
                    "reason": "reply:linkedin", "detail": {"event": ev.get("external_id")}}):
                for r in em_rows:
                    ok = await replyio_client.remove_from_campaign(
                        campaign_id=int(r["campaign_id"]), email=r["email"])
                    stats["stopped_email" if ok else "stop_failures"] += 1

    if latest and latest != cursor:
        engagement_repo.set_setting(CURSOR_KEY, latest)
    if stats["checked"]:
        logger.info("stop-rule sweep: %s", stats)
    return stats
