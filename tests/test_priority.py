"""Buying-intent scoring — deterministic tiers + explainable reasons (priority.py)."""

from datetime import UTC, datetime, timedelta

from auto_search import priority

NOW = datetime(2026, 6, 10, tzinfo=UTC)


def _recent(days: int = 1) -> str:
    return (NOW - timedelta(days=days)).isoformat()


def _job(title: str, tier: str = "core", days: int = 1) -> dict:
    return {"signal_type": "job_posting", "title": title, "tier": tier,
            "observed_at": _recent(days)}


def _sig(stype: str, days: int = 1) -> dict:
    return {"signal_type": stype, "observed_at": _recent(days)}


# ── single roles stay Watch (the flood we stop) ────────────────────────


def test_single_standard_role_is_watch():
    i = priority.intent([_job("Medical Biller", tier="standard")], now=NOW)
    assert i.tier == "watch" and i.score < 65


def test_single_core_role_is_watch():
    assert priority.intent([_job("Denials Specialist")], now=NOW).tier == "watch"


def test_single_leader_hire_is_strong_but_not_hot_alone():
    i = priority.intent([_job("Director of Revenue Cycle", tier="standard")], now=NOW)
    assert "leader" in i.reason and i.tier == "watch"


# ── real intent goes Hot ───────────────────────────────────────────────


def test_build_out_three_core_roles_is_hot():
    i = priority.intent([_job("Denials Spec"), _job("AR Spec"), _job("Eligibility Spec")], now=NOW)
    assert i.tier == "hot" and i.score >= 65
    assert "3 RCM roles open" in i.reason


def test_new_exec_alone_is_hot():
    i = priority.intent([_sig("leadership_change")], now=NOW)
    assert i.tier == "hot" and "new exec" in i.reason


def test_engaged_exec_is_hot():
    assert priority.intent([_sig("social_engagement")], now=NOW).tier == "hot"


def test_leader_hire_plus_abm_is_hot():
    i = priority.intent([_job("VP Revenue Cycle")], abm_confirmed=True, now=NOW)
    assert i.tier == "hot" and "ABM target" in i.reason


def test_multi_signal_type_is_hot_and_leads_with_strongest():
    i = priority.intent([_job("Medical Biller", tier="standard"), _sig("leadership_change")], now=NOW)
    assert i.tier == "hot"
    assert i.reason.startswith("new exec") and "multi-signal" in i.reason


# ── mechanics ──────────────────────────────────────────────────────────


def test_empty_is_zero_watch():
    i = priority.intent([], now=NOW)
    assert i.score == 0 and i.tier == "watch"


def test_score_capped_at_100():
    sigs = [_sig("leadership_change")] + [_job(f"Denials {n}") for n in range(5)]
    i = priority.intent(sigs, abm_confirmed=True, now=NOW)
    assert i.score == 100 and i.tier == "hot"


def test_stale_signals_lose_the_recency_bonus():
    fresh = priority.intent([_job("Denials Spec")], now=NOW)
    stale = priority.intent([_job("Denials Spec", days=30)], now=NOW)
    assert fresh.score == stale.score + 5


def test_last_signal_at_is_the_freshest():
    assert priority.last_signal_at([_job("A", days=10), _job("B", days=2)]) \
        == datetime.fromisoformat(_recent(2))


def test_hot_threshold_is_env_tunable(monkeypatch):
    # a single core role scores 30 + 5 recency = 35 (Watch at the default 65)
    assert priority.intent([_job("Denials Spec")], now=NOW).tier == "watch"
    monkeypatch.setenv("DISCOVERY_INTENT_HOT", "35")
    assert priority.intent([_job("Denials Spec")], now=NOW).tier == "hot"
