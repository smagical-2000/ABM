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
    assert card["blocks"][0]["text"]["text"] == "Acme Health — Hot"   # no emoji
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


def test_card_signals_are_counts_no_emoji_no_timeline():
    # signals = per-kind counts only; no per-touch rows, no emoji
    card = notify.build_card(_acct(), _events())
    blob = json.dumps(card, ensure_ascii=False)
    assert "*Signals:* High-intent lead 1 · Podcast 1 · Click 1" in blob
    assert "Recent touches" not in blob               # no per-touch spam
    for emoji in ("🔥", "📝", "👆", "🎙️", "🤝", "🎪"):
        assert emoji not in blob                       # professional, emoji-free
    assert "+10 pts" not in blob and "2026-06-08" not in blob   # no per-touch dates/points


def test_test_flag_marks_message():
    card = notify.build_card(_acct(), _events(), test=True)
    assert card["blocks"][0]["text"]["text"] == "[TEST] Acme Health — Hot"


def test_app_url_adds_button():
    card = notify.build_card(_acct(), _events(), app_url="https://x.example/eng")
    blob = json.dumps(card, ensure_ascii=False)
    assert "Open in console" in blob and "https://x.example/eng" in blob


def test_card_includes_decision_makers():
    dms = [{"name": "Jane Doe", "title": "VP Revenue Cycle", "email": "jane@acme.com", "phone": "+1 555 1"},
           {"name": "John Roe", "title": "CFO", "email": None, "phone": None}]
    card = notify.build_card(_acct(), _events(), dms=dms)
    blob = json.dumps(card, ensure_ascii=False)
    assert "Decision-makers" in blob
    assert "Jane Doe" in blob and "VP Revenue Cycle" in blob and "jane@acme.com" in blob
    assert "no contact info found" in blob          # John has neither
    assert "<@" not in blob                          # still never pings anyone


def test_card_no_dms_section_when_none():
    card = notify.build_card(_acct(), _events())
    assert "Decision-makers" not in json.dumps(card, ensure_ascii=False)


def test_scheme_less_app_url_adds_no_button():
    # Slack rejects scheme-less button URLs (invalid_blocks) — guard against it
    card = notify.build_card(_acct(), _events(), app_url="example.com/eng")
    assert "Open in console" not in json.dumps(card, ensure_ascii=False)
    assert not any(b.get("type") == "actions" for b in card["blocks"])


def test_post_card_no_webhook_returns_false(monkeypatch):
    monkeypatch.delenv("SLACK_ENGAGEMENT_WEBHOOK", raising=False)
    assert notify.post_card({"text": "x"}) is False
