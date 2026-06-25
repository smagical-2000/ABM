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
    assert "*Signals:* BOFU 1 · Podcast 1 · Click 1" in blob
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


# ── AE routing (Hot account → owner) ────────────────────────────────────────

def test_resolve_ae_owner_with_slack_id_pings():
    ae = notify.resolve_ae(_acct(framework="health_system"), owner_name="Alykhan Jina",
                           ids={"Alykhan Jina": "U01"}, by_specialty={})
    assert ae == "<@U01>"


def test_resolve_ae_specialty_fallback_when_no_owner():
    ae = notify.resolve_ae(_acct(framework="payer"), ids={"Manu Gupta": "U02"},
                           by_specialty={"payer": "Manu Gupta"})
    assert ae == "<@U02>"


def test_resolve_ae_plain_name_when_no_slack_id():
    ae = notify.resolve_ae(_acct(), owner_name="Aly J", ids={}, by_specialty={})
    assert ae == "@Aly J"          # names them, does not ping


def test_resolve_ae_none_when_unmapped(monkeypatch):
    monkeypatch.delenv("DEFAULT_AE", raising=False)
    assert notify.resolve_ae(_acct(framework="specialty"), ids={}, by_specialty={}) is None


def test_resolve_ae_falls_back_to_default(monkeypatch):
    # An unscored (no-framework) Hot account still tags someone via DEFAULT_AE.
    monkeypatch.setenv("DEFAULT_AE", "Alykhan")
    assert notify.resolve_ae({}, ids={}, by_specialty={}) == "@Alykhan"


def test_resolve_ae_uses_raw_framework_key_not_label():
    # The engaged-account dict carries the human label in `framework` ("Health System")
    # and the raw key in `framework_key` — SPECIALTY_AE is keyed by the raw key. Resolve
    # must use framework_key, or the AE lead line never fires (the H2 regression).
    acct = {"framework": "Health System", "framework_key": "health_system"}
    ae = notify.resolve_ae(acct, ids={"Alykhan Jina": "U01"},
                           by_specialty={"health_system": "Alykhan Jina"})
    assert ae == "<@U01>"


def test_card_ae_lead_line_and_real_ping():
    card = notify.build_card(_acct(name="Ochsner Health System"), _events(), ae="<@U01>")
    blob = json.dumps(card, ensure_ascii=False)
    assert "<@U01> your account *Ochsner Health System* — move to status Hot" in blob


def test_card_dm_limit_caps_decision_makers():
    dms = [{"name": f"P{i}", "title": "VP"} for i in range(5)]
    card = notify.build_card(_acct(), _events(), dms=dms, dm_limit=2)
    blob = json.dumps(card, ensure_ascii=False)
    assert "P0" in blob and "P1" in blob and "P2" not in blob
    assert not any(b.get("type") == "actions" for b in card["blocks"])


def test_post_card_no_webhook_returns_false(monkeypatch):
    monkeypatch.delenv("SLACK_ENGAGEMENT_WEBHOOK", raising=False)
    assert notify.post_card({"text": "x"}) is False


# ── SDR routing (Warm account → SDR) ────────────────────────────────────────

def test_resolve_sdr_specialty_with_slack_id():
    sdr = notify.resolve_sdr(_acct(framework_key="health_system"),
                             ids={"Ben Davies": "U10"},
                             by_specialty={"health_system": "Ben Davies"})
    assert sdr == "<@U10>"


def test_resolve_sdr_plain_name_when_no_slack_id():
    sdr = notify.resolve_sdr(_acct(framework_key="payer"),
                             ids={}, by_specialty={"payer": "Gabriel"})
    assert sdr == "@Gabriel"


def test_resolve_sdr_none_when_unmapped(monkeypatch):
    monkeypatch.delenv("DEFAULT_SDR", raising=False)
    assert notify.resolve_sdr(_acct(framework_key="specialty"),
                              ids={}, by_specialty={}) is None


def test_resolve_sdr_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("DEFAULT_SDR", "Ben Davies")
    assert notify.resolve_sdr({}, ids={}, by_specialty={}) == "@Ben Davies"


def test_resolve_sdr_uses_framework_key_not_label():
    acct = {"framework": "Health System", "framework_key": "health_system"}
    sdr = notify.resolve_sdr(acct, ids={"Ben Davies": "U10"},
                             by_specialty={"health_system": "Ben Davies"})
    assert sdr == "<@U10>"


def test_framework_from_segment_mapping():
    f = notify._framework_from_segment
    assert f("Payer") == "payer" and f("Payers") == "payer"
    assert f("Specialties") == "specialty" and f("Specialty - Ortho") == "specialty"
    assert f("Health Systems") == "health_system"
    assert f("Independent Hospitals") == "health_system"
    assert f("") is None and f(None) is None and f("Physician Group") is None


def test_resolve_ae_falls_back_to_segment_when_no_framework():
    """An engaged-but-unscored account (no framework_key) routes by its ABM segment."""
    acct = {"segment": "Specialties"}                       # no framework_key/framework
    ae = notify.resolve_ae(acct, ids={"Justin Pride": "U1"},
                           by_specialty={"specialty": "Justin Pride"})
    assert ae == "<@U1>"


def test_resolve_sdr_falls_back_to_segment_when_no_framework():
    acct = {"segment": "Independent Hospitals"}             # → health_system
    sdr = notify.resolve_sdr(acct, ids={"Ben Davies": "U2"},
                             by_specialty={"health_system": "Ben Davies"})
    assert sdr == "<@U2>"


def test_framework_key_wins_over_segment():
    """An explicit framework_key always beats the segment fallback."""
    acct = {"framework_key": "payer", "segment": "Specialties"}
    ae = notify.resolve_ae(acct, ids={}, by_specialty={"payer": "Matt", "specialty": "Jp"})
    assert ae == "@Matt"


# ── live routing: real channels + real pings, gated by a flag ────────────────


def test_live_routing_off_by_default(monkeypatch):
    monkeypatch.delenv("ENGAGEMENT_LIVE_ROUTING", raising=False)
    assert notify.live_routing() is False


def test_live_routing_on_when_flag_set(monkeypatch):
    monkeypatch.setenv("ENGAGEMENT_LIVE_ROUTING", "1")
    assert notify.live_routing() is True


def test_tier_webhook_routes_by_tier(monkeypatch):
    """tier_webhook maps tier→channel (the endpoint decides whether to use it)."""
    monkeypatch.setenv("SLACK_AE_WEBHOOK", "https://hooks.test/ae")
    monkeypatch.setenv("SLACK_SDR_WEBHOOK", "https://hooks.test/sdr")
    assert notify.tier_webhook(is_ae=True) == "https://hooks.test/ae"      # Hot → AE channel
    assert notify.tier_webhook(is_ae=False) == "https://hooks.test/sdr"    # Warm/Some → SDR


def test_tier_webhook_none_when_unset(monkeypatch):
    monkeypatch.delenv("SLACK_AE_WEBHOOK", raising=False)
    monkeypatch.delenv("SLACK_SDR_WEBHOOK", raising=False)
    assert notify.tier_webhook(is_ae=True) is None


def test_resolve_uses_env_ids_when_none_but_plain_when_empty(monkeypatch):
    """ids=None means 'use SDR_SLACK_IDS env' (real ping); ids={} means plain @Name —
    this is how the endpoint switches pings off for testing without unsetting env."""
    monkeypatch.setenv("SDR_SLACK_IDS", "Gabriel=U096")
    monkeypatch.setenv("SPECIALTY_SDR", "payer=Gabriel")
    assert notify.resolve_sdr(_acct(framework_key="payer"), ids=None) == "<@U096>"
    assert notify.resolve_sdr(_acct(framework_key="payer"), ids={}) == "@Gabriel"


def test_card_sdr_lead_line_for_warm():
    card = notify.build_card(_acct(name="Baptist Health", tier="Warm", score=14),
                             _events(), ae="@Ben Davies")
    blob = json.dumps(card, ensure_ascii=False)
    assert "@Ben Davies your account *Baptist Health* — move to status Warm" in blob


# ── SDR intel brief (deep-research Option 1) ─────────────────────────────────


def _scored_with_research():
    return {
        "name": "Acme Health",
        "discovery_signals": [
            {"signal_type": "job_posting", "summary": "Hiring: Prior Auth Specialist — Fruita, CO"},
            {"signal_type": "leadership", "summary": "New VP Revenue Cycle started"},
            {"summary": "Funding: $20M Series B"},
            {"summary": "fourth signal — should be dropped (cap 3)"},
        ],
        "dossier": {
            "entry_strategy": {
                "timing": "HIGH - multi-facility billing complexity suggests immediate RCM pain",
                "primary_angles": ["Lead with anesthesia-specific charge capture optimization",
                                   "Second angle — should not appear"],
            },
            "recent_news": [
                {"headline": "BBB complaints highlight billing strain", "date": "2025"},
                {"headline": "Continued operation across 17 facilities", "date": "2024-2025"},
                {"headline": "third — should be dropped (cap 2)", "date": "x"},
            ],
        },
    }


def test_summarize_research_extracts_brief():
    r = notify.summarize_research(_scored_with_research())
    assert r["why_now"].startswith("HIGH - multi-facility")
    assert r["triggers"] == ["Hiring: Prior Auth Specialist — Fruita, CO",
                             "New VP Revenue Cycle started", "Funding: $20M Series B"]  # cap 3
    assert [n["headline"] for n in r["news"]] == ["BBB complaints highlight billing strain",
                                                  "Continued operation across 17 facilities"]  # cap 2
    assert r["angle"] == "Lead with anesthesia-specific charge capture optimization"


def test_summarize_research_empty_when_none():
    assert notify.summarize_research(None) == {}
    assert notify.summarize_research({}) == {}
    assert notify.summarize_research({"discovery_signals": [], "dossier": {}}) == {}


def test_summarize_research_is_null_safe():
    # malformed/partial stored research must never crash or leak junk
    scored = {"discovery_signals": [None, {"summary": None}, "plain string", {"nope": 1}],
              "dossier": {"recent_news": [None, "str", {"headline": ""}, {"headline": "Real"}],
                          "entry_strategy": {"timing": None, "primary_angles": []}}}
    r = notify.summarize_research(scored)
    assert r.get("triggers") == ["plain string"]
    assert [n["headline"] for n in r.get("news", [])] == ["Real"]
    assert "why_now" not in r and "angle" not in r


def test_summarize_research_dedupes_triggers_and_drops_no_news():
    scored = {
        "discovery_signals": [
            {"summary": "Hiring: RCM Analyst — Denver, CO"},
            {"summary": "Hiring: RCM Analyst — Austin, TX"},   # same role, dropped
            {"summary": "Funding: Series A"},
        ],
        "dossier": {"recent_news": [
            {"headline": "No significant M&A identified in 2024-2025"},   # negative finding, dropped
            {"headline": "Opened new Phoenix clinic", "date": "2025"},
        ]},
    }
    r = notify.summarize_research(scored)
    assert r["triggers"] == ["Hiring: RCM Analyst — Denver, CO", "Funding: Series A"]
    assert [n["headline"] for n in r["news"]] == ["Opened new Phoenix clinic"]


def test_summarize_research_survives_nondict_shapes():
    # truthy-but-wrong JSONB shapes must degrade to {}, never raise
    assert notify.summarize_research("a string") == {}
    assert notify.summarize_research(["a", "list"]) == {}
    assert notify.summarize_research({"dossier": "oops", "discovery_signals": "nope"}) == {}
    assert notify.summarize_research({"dossier": {"entry_strategy": ["bad"],
                                                  "recent_news": "bad"},
                                      "discovery_signals": {"bad": 1}}) == {}


def test_summarize_research_trims_long_text():
    long = "x" * 500
    r = notify.summarize_research({"dossier": {"entry_strategy": {"timing": long}}})
    assert len(r["why_now"]) == 240 and r["why_now"].endswith("…")


def test_card_renders_intel_block_no_emoji():
    card = notify.build_card(_acct(), _events(), research=notify.summarize_research(_scored_with_research()))
    blob = json.dumps(card, ensure_ascii=False)
    assert "Account intel" in blob
    assert "*Why now:* HIGH - multi-facility" in blob
    assert "*Triggers:*" in blob and "Hiring: Prior Auth Specialist" in blob
    assert "*Recent news:*" in blob and "BBB complaints highlight billing strain" in blob
    assert "*Opening angle:* Lead with anesthesia-specific" in blob
    assert any(b.get("type") == "divider" for b in card["blocks"])   # separated from heat
    for emoji in ("🔥", "📝", "👆", "🎙️", "🤝", "🎪", "🚀", "💡"):
        assert emoji not in blob


def test_card_no_intel_block_when_no_research():
    card = notify.build_card(_acct(), _events())
    blob = json.dumps(card, ensure_ascii=False)
    assert "Account intel" not in blob
    assert not any(b.get("type") == "divider" for b in card["blocks"])
