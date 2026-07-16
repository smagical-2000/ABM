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
