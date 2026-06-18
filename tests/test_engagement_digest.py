"""Weekly digest — pure select_movers + build_digest. No I/O."""

from __future__ import annotations

import json

from auto_search.engagement import digest


def _ev(account_id, kind, points, company):
    return {"account_id": account_id, "kind": kind, "points": points, "company": company}


def test_select_movers_ranks_upgrades_then_gain():
    window = [
        _ev("a", "click", 1, "Acme"),                        # click-only → NOT a mover
        _ev("b", "sales_accepted_opportunity", 10, "Beta"),  # big, and crosses a tier
        _ev("c", "high_intent_lead", 10, "Gamma"),
    ]
    # b: lifetime 10 from 0 (Lower->Some upgrade), c: lifetime 40 — was 30 (Hot) already
    scores = {"a": 5, "b": 10, "c": 40}
    movers = digest.select_movers(window, scores)
    assert [m["account_id"] for m in movers] == ["b", "c"]   # 'a' (click-only) excluded
    b = movers[0]
    assert b["name"] == "Beta" and b["tier"] == "Some" and b["upgraded"] is True
    assert b["reason"] == "Sales accepted opp"
    assert movers[1]["upgraded"] is False        # c already Hot before this week


def test_select_movers_excludes_noise_only_accounts():
    # an account whose only window touches are clicks + TOFU content is NOT "heated up"
    window = [_ev("n", "click", 1, "Noise Co"), _ev("n", "low_intent_lead", 2, "Noise Co"),
              _ev("m", "click", 1, "Mix Co"), _ev("m", "reply", 6, "Mix Co")]
    movers = digest.select_movers(window, {"n": 3, "m": 7})
    assert [m["account_id"] for m in movers] == ["m"]   # only the one with a real touch
    assert movers[0]["gained"] == 7 and movers[0]["reason"] == "Replied"  # clicks still summed


def test_select_movers_one_reason_is_highest_value_touch():
    window = [_ev("x", "click", 1, "X"), _ev("x", "click", 1, "X"),
              _ev("x", "meeting_booked", 10, "X")]
    movers = digest.select_movers(window, {"x": 12})
    assert len(movers) == 1
    assert movers[0]["gained"] == 12                 # all touches summed
    assert movers[0]["reason"] == "Meeting booked"   # but only the top reason shown


def test_select_movers_skips_zero_gain_and_no_account():
    window = [_ev(None, "click", 1, "Nope"), _ev("z", "delivered", 0, "Z")]
    assert digest.select_movers(window, {}) == []


def test_build_digest_is_lean_and_emoji_free():
    movers = [{"account_id": "b", "name": "Beta", "tier": "Hot", "score": 24,
               "gained": 10, "upgraded": True, "reason": "Sales accepted opp"},
              {"account_id": "c", "name": "Gamma", "tier": "Warm", "score": 12,
               "gained": 6, "upgraded": False, "reason": "Replied"}]
    card = digest.build_digest(movers, limit=5, console_url="https://x.example/eng")
    blob = json.dumps(card, ensure_ascii=False)
    assert "2 accounts heated up this week" in blob
    assert "*Beta* — Hot · new · Sales accepted opp" in blob   # 'new' = tier upgrade
    assert "*Gamma* — Warm · Replied" in blob
    assert "Open console" in blob and "https://x.example/eng" in blob
    # lean: no rates / contact counts / per-touch dump
    for noise in ("open rate", "contacts", "delivered", "clicks", "pts this wk"):
        assert noise not in blob.lower()
    for emoji in ("🔥", "📈", "🎯", "🚀"):
        assert emoji not in blob


def test_build_digest_caps_list_and_shows_more():
    movers = [{"account_id": str(i), "name": f"Co{i}", "tier": "Hot", "score": 30,
               "gained": 10, "upgraded": False, "reason": "Meeting booked"} for i in range(8)]
    card = digest.build_digest(movers, limit=5)
    blob = json.dumps(card, ensure_ascii=False)
    assert "8 accounts heated up" in blob
    assert "+3 more in the console" in blob
    # only 5 listed inline
    assert blob.count("Meeting booked") == 5


def test_build_digest_empty_week():
    card = digest.build_digest([], limit=5)
    blob = json.dumps(card, ensure_ascii=False)
    assert "No accounts heated up this week" in blob
    assert card["text"].startswith("0 account")


def test_build_digest_test_flag():
    card = digest.build_digest([], test=True)
    assert card["blocks"][0]["text"]["text"].startswith("[TEST]")
