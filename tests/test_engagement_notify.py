"""Slack activation card — pure build_card. No I/O."""

from __future__ import annotations

import json

from auto_search.engagement import notify


def _acct(**kw):
    base = {"account_id": "abm_acme", "name": "Acme Health", "tier": "Hot",
            "score": 24, "segment": "Health Systems", "domain": "acme.com",
            "lists": ["abm"]}
    base.update(kw)
    return base


def _events():
    return [
        {"kind": "high_intent_lead", "points": 10, "occurred_at": "2026-06-08T00:00:00Z"},
        {"kind": "podcast_lead", "points": 4, "occurred_at": "2026-03-18T00:00:00Z"},
        {"kind": "click", "points": 1, "occurred_at": "2026-05-16T00:00:00Z"},
    ]


def test_card_header_and_heat():
    card = notify.build_card(_acct(), _events())
    blob = json.dumps(card, ensure_ascii=False)
    assert card["blocks"][0]["type"] == "header"
    assert "Acme Health is Hot" in card["blocks"][0]["text"]["text"]
    assert "24 pts (Hot)" in blob
    assert card["text"] == "Acme Health is Hot (24 pts)"


def test_card_never_pings_anyone():
    # plain-text SDR, never an encoded <@id> mention
    card = notify.build_card(_acct(), _events(), sdr="gabriel")
    blob = json.dumps(card, ensure_ascii=False)
    assert "*SDR:* gabriel" in blob
    assert "<@" not in blob and "<!channel>" not in blob and "<!here>" not in blob


def test_classification_prefers_scored_framework_and_tier():
    card = notify.build_card(_acct(framework="health_system", fit_tier="Tier 1"), _events())
    assert "Health System · Tier 1" in json.dumps(card, ensure_ascii=False)


def test_junk_segment_suppressed():
    card = notify.build_card(_acct(segment="Matches", framework=None), _events())
    assert "Matches" not in json.dumps(card, ensure_ascii=False)        # never show the import artifact


def test_timeline_is_newest_first_with_points():
    card = notify.build_card(_acct(), _events())
    blob = json.dumps(card, ensure_ascii=False)
    assert "High-intent lead · 2026-06-08 · +10" in blob
    # newest (Jun 8) appears before oldest shown (Mar 18)
    assert blob.index("2026-06-08") < blob.index("2026-03-18")


def test_test_flag_marks_message():
    card = notify.build_card(_acct(), _events(), test=True)
    assert "🧪 [test]" in card["blocks"][0]["text"]["text"]


def test_app_url_adds_button():
    card = notify.build_card(_acct(), _events(), app_url="https://x.example/eng")
    blob = json.dumps(card, ensure_ascii=False)
    assert "Open in console" in blob and "https://x.example/eng" in blob


def test_scheme_less_app_url_adds_no_button():
    # Slack rejects scheme-less button URLs (invalid_blocks) — guard against it
    card = notify.build_card(_acct(), _events(), app_url="example.com/eng")
    assert "Open in console" not in json.dumps(card, ensure_ascii=False)
    assert not any(b.get("type") == "actions" for b in card["blocks"])


def test_post_card_no_webhook_returns_false(monkeypatch):
    monkeypatch.delenv("SLACK_ENGAGEMENT_WEBHOOK", raising=False)
    assert notify.post_card({"text": "x"}) is False
