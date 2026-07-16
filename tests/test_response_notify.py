"""Outreach response -> Slack: the positive-only filter and card builders."""

from __future__ import annotations

from auto_search.campaigns import response_notify as rn


def test_smartlead_positive_category_builds_card():
    card = rn.smartlead_event_to_card({
        "event_type": "LEAD_CATEGORY_UPDATED", "campaign_name": "Health Systems",
        "first_name": "Jane", "last_name": "Doe", "lead_email": "jane@hosp.org",
        "company_name": "Mercy Health", "lead_category": {"new_name": "Interested"},
        "reply_message": {"text": "Yes, tell me more about the pricing."}})
    assert card and "Positive email reply" in str(card)
    text = str(card)
    assert "Jane Doe" in text and "Mercy Health" in text and "Interested" in text


def test_smartlead_negative_and_uncategorized_dropped():
    assert rn.smartlead_event_to_card({"event_type": "EMAIL_REPLY",
                                       "reply_category": "Not Interested"}) is None
    assert rn.smartlead_event_to_card({"event_type": "EMAIL_REPLY",
                                       "first_name": "Bo"}) is None       # no category yet
    assert rn.smartlead_event_to_card({"event_type": "EMAIL_OPEN"}) is None
    assert rn.smartlead_event_to_card({"event_type": "LEAD_UNSUBSCRIBED",
                                       "lead_category": "Do Not Contact"}) is None


def test_heyreach_reply_builds_card_and_accept_dropped():
    card = rn.heyreach_event_to_card({
        "eventType": "MESSAGE_REPLY_RECEIVED",
        "lead": {"firstName": "Sam", "lastName": "Lee", "companyName": "OrthoCo",
                 "profileUrl": "https://www.linkedin.com/in/sam-lee"},
        "campaign": {"id": 505509, "name": "Health Systems - LinkedIn"},
        "message": {"text": "Thanks for reaching out, happy to chat."}})
    assert card and "LinkedIn response" in str(card)
    assert "Sam Lee" in str(card) and "OrthoCo" in str(card)
    assert rn.heyreach_event_to_card({"eventType": "CONNECTION_REQUEST_ACCEPTED",
                                      "lead": {"firstName": "A"}}) is None
    assert rn.heyreach_event_to_card({"eventType": "MESSAGE_SENT"}) is None


def test_post_card_never_raises_without_webhook(monkeypatch):
    monkeypatch.delenv("SLACK_OUTREACH_WEBHOOK", raising=False)
    assert rn.post_card({"blocks": []}) is False
    assert rn.post_card(None) is False


def test_cards_carry_inbox_deep_links():
    sl = rn.smartlead_event_to_card({"event_type": "EMAIL_REPLY",
                                     "lead_category": "Interested",
                                     "first_name": "A", "campaign_name": "X"})
    assert rn.SMARTLEAD_INBOX in str(sl)
    sl2 = rn.smartlead_event_to_card({"event_type": "LEAD_CATEGORY_UPDATED",
                                      "lead_category": "Interested",
                                      "app_url": "https://app.smartlead.ai/app/inbox/123"})
    assert "inbox/123" in str(sl2)          # payload deep link wins when present
    hr = rn.heyreach_event_to_card({"eventType": "MESSAGE_REPLY_RECEIVED",
                                    "lead": {"firstName": "B"}, "campaign": {}})
    assert rn.HEYREACH_INBOX in str(hr)


def test_smartlead_engagement_mapping():
    click = rn.smartlead_event_to_engagement({
        "event_type": "EMAIL_LINK_CLICK", "lead_email": "Jane@Hosp.org",
        "company_name": "Mercy Health", "campaign_name": "Health Systems"})
    assert click["kind"] == "outbound_click"
    assert click["email"] == "jane@hosp.org"          # lowercased
    assert click["company"] == "Mercy Health"
    assert click["campaign"] == "Health Systems"
    assert click["external_id"] == "outbound:outbound_click:jane@hosp.org"
    # a raw reply adds NOTHING — heat lands only on positive categorization
    assert rn.smartlead_event_to_engagement({
        "event_type": "EMAIL_REPLY", "lead_email": "j@h.org"}) is None
    pos = rn.smartlead_event_to_engagement({
        "event_type": "LEAD_CATEGORY_UPDATED", "lead_email": "j@h.org",
        "lead_category": {"new_name": "Interested"}})
    assert pos["kind"] == "outbound_reply"
    # Meeting Request is a positive reply (6), NOT a booked meeting (10)
    mreq = rn.smartlead_event_to_engagement({
        "event_type": "LEAD_CATEGORY_UPDATED", "lead_email": "j@h.org",
        "lead_category": {"new_name": "Meeting Request"}})
    assert mreq["kind"] == "outbound_reply"
    meet = rn.smartlead_event_to_engagement({
        "event_type": "LEAD_CATEGORY_UPDATED", "lead_email": "j@h.org",
        "lead_category": {"new_name": "Meeting Booked"}})
    assert meet["kind"] == "outbound_meeting_booked"


def test_smartlead_engagement_mapping_drops_noise():
    # negative category, opens, missing email -> no touch
    assert rn.smartlead_event_to_engagement({
        "event_type": "LEAD_CATEGORY_UPDATED", "lead_email": "j@h.org",
        "lead_category": "Not Interested"}) is None
    assert rn.smartlead_event_to_engagement({
        "event_type": "EMAIL_OPEN", "lead_email": "j@h.org"}) is None
    assert rn.smartlead_event_to_engagement({
        "event_type": "EMAIL_REPLY"}) is None


def test_outbound_kinds_scored():
    from auto_search.engagement import scoring
    assert scoring.points_for("outbound_click") == 1
    assert scoring.points_for("outbound_reply") == 6
    assert scoring.points_for("outbound_meeting_booked") == 10


# ── QA round 2 (2026-07-15 adversarial agent) — each fix guarded ─────────


def test_click_event_with_stale_positive_category_posts_no_card():
    """MAJOR guard: click/open events echo the lead's CURRENT category — an
    already-Interested lead's bot click must not post a false reply card."""
    assert rn.smartlead_event_to_card({
        "event_type": "EMAIL_LINK_CLICK", "lead_category": "Interested",
        "first_name": "Bot", "campaign_name": "X"}) is None
    assert rn.smartlead_event_to_card({
        "event_type": "EMAIL_OPEN", "lead_category": "Interested"}) is None


def test_mrkdwn_injection_escaped():
    """MAJOR guard: lead-controlled strings can't smuggle live links/format."""
    card = rn.smartlead_event_to_card({
        "event_type": "EMAIL_REPLY", "lead_category": "Interested",
        "first_name": "<https://evil.example|Open in SmartLead inbox>",
        "company_name": "Acme & Sons",
        "reply_message": {"text": "click <https://evil.example|here> now"}})
    text = str(card)
    assert "<https://evil.example" not in text
    assert "&lt;https://evil.example" in text
    assert "Acme &amp; Sons" in text
    # the legit inbox link is still a real link
    assert f"<{rn.SMARTLEAD_INBOX}|" in text


def test_heyreach_every_message_reply_variant_cards():
    card = rn.heyreach_event_to_card({
        "eventType": "EVERY_MESSAGE_REPLY_RECEIVED",
        "lead": {"firstName": "Re", "lastName": "Plier"}, "campaign": {}})
    assert card and "LinkedIn response" in str(card)


def test_engagement_touch_prefers_payload_timestamp():
    t = rn.smartlead_event_to_engagement({
        "event_type": "EMAIL_LINK_CLICK", "lead_email": "j@h.org",
        "event_timestamp": "2026-07-10T08:00:00Z"})
    assert t["occurred_at"] == "2026-07-10T08:00:00Z"
    assert t["raw"]["event_timestamp"] == "2026-07-10T08:00:00Z"   # full payload landed
