"""Engagement events -> scorer intent signals: the guarantee that a score can
never claim "no intent signals" about an account that booked a meeting with us."""

from auto_search.engagement import intent_feed
from auto_search.scoring.models import Account
from auto_search.scoring.service import ScoringService


def _e(kind, when, points=1):
    return {"kind": kind, "occurred_at": when, "points": points}


def test_meeting_booked_leads_and_is_dated():
    sigs = intent_feed.to_intent_signals([
        _e("click", "2026-05-01T10:00:00"),
        _e("meeting_booked", "2026-06-25T18:00:00"),
        _e("low_intent_lead", "2026-01-25T09:00:00"),
    ])
    assert sigs[0]["signal_type"] == "engagement_meeting_booked"   # strongest first
    assert "2026-06-25" in sigs[0]["summary"]
    assert "meeting" in sigs[0]["summary"].lower()


def test_repeats_collapse_with_count_and_latest_date():
    sigs = intent_feed.to_intent_signals([
        _e("linkedin_tofu", "2026-06-23"),
        _e("linkedin_tofu", "2026-06-24"),
        _e("linkedin_tofu", "2026-07-02"),
    ])
    assert len(sigs) == 1
    assert "x3" in sigs[0]["summary"] and "2026-07-02" in sigs[0]["summary"]


def test_zero_point_and_deprecated_kinds_never_appear():
    sigs = intent_feed.to_intent_signals([
        _e("delivered", "2026-06-01"), _e("open", "2026-06-02"),
        _e("bounce", "2026-06-03"), _e("sales_accepted_opportunity", "2026-06-04"),
    ])
    assert sigs == []


def test_limit_and_empty():
    events = [_e(k, "2026-06-01") for k in
              ("click", "reply", "meeting_booked", "podcast_lead",
               "low_intent_lead", "linkedin_tofu", "high_intent_lead")]
    assert len(intent_feed.to_intent_signals(events, limit=3)) == 3
    assert intent_feed.to_intent_signals([]) == []
    assert intent_feed.to_intent_signals(None) == []


# ── score-time merge in ScoringService ────────────────────────────────


def _account(**kw):
    base = dict(account_id="acc_x", name="X Health", segment="specialty",
                framework="specialty", source="ae")
    base.update(kw)
    return Account(**base)


def test_service_merges_own_signals_deduped():
    svc = ScoringService(repo=None, own_signals=lambda a: [
        {"signal_type": "engagement_meeting_booked", "summary": "Booked", "url": None},
        {"signal_type": "s", "summary": "already there", "url": None},
    ])
    a = _account(discovery_signals=[{"signal_type": "s", "summary": "already there"}])
    svc._merge_own_signals(a)
    types = [s["signal_type"] for s in a.discovery_signals]
    assert types == ["s", "engagement_meeting_booked"]   # merged, not duplicated


def test_service_own_signals_failure_never_blocks():
    def boom(_a):
        raise RuntimeError("engagement store down")
    svc = ScoringService(repo=None, own_signals=boom)
    a = _account()
    svc._merge_own_signals(a)          # must not raise
    assert a.discovery_signals == []


def test_service_without_provider_is_a_noop():
    svc = ScoringService(repo=None)
    a = _account()
    svc._merge_own_signals(a)
    assert a.discovery_signals == []
