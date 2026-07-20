"""MAR2: LinkedIn connection-request acceptance scoring.

Accepting a connection request that carried our personalized note is a warm,
intent-bearing signal (linkedin_connect_message = 10) — distinct from a bare
accept (linkedin_connect = 2). All 5 live HeyReach campaigns send a note
(verified 2026-07-18), so their accepts score 10; the score is resolved from the
webhook payload (if HeyReach echoes the note) or the campaign allowlist setting.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from auto_search.campaigns import response_notify as rn
from auto_search.engagement import scoring

_app = importlib.import_module("auto_search.api.app")


# ── pure: kind mapping ───────────────────────────────────────────────────


def test_connect_accept_is_10():
    # Sunny 2026-07-18: every accept is a messaged connect -> 10, no bare-2 tier.
    assert rn.heyreach_event_kind("CONNECTION_REQUEST_ACCEPTED") == "linkedin_connect_message"
    assert scoring.points_for("linkedin_connect_message") == 10


def test_reply_events_map():
    for et in ("MESSAGE_REPLY_RECEIVED", "EVERY_MESSAGE_REPLY_RECEIVED",
               "INMAIL_REPLY_RECEIVED"):
        assert rn.heyreach_event_kind(et) == "linkedin_reply"
    assert scoring.points_for("linkedin_reply") == 6


def test_case_insensitive_and_unknown_ignored():
    assert rn.heyreach_event_kind("connection_request_accepted") == "linkedin_connect_message"
    assert rn.heyreach_event_kind("SEEN_BY_LEAD") is None
    assert rn.heyreach_event_kind("") is None


# ── pure: payload note extraction ────────────────────────────────────────


def test_note_top_level_and_variants():
    assert rn.heyreach_connect_message({"connectionMessage": "Hi, let's connect!"}) == "Hi, let's connect!"
    assert rn.heyreach_connect_message({"connectMessage": "hey"}) == "hey"
    assert rn.heyreach_connect_message({"note": "warm intro"}) == "warm intro"


def test_note_inside_lead_and_dict_shape():
    assert rn.heyreach_connect_message({"lead": {"connectionMessage": "hello there"}}) == "hello there"
    assert rn.heyreach_connect_message({"message": {"text": "dict-shaped"}}) == "dict-shaped"


def test_note_absent_or_blank_or_bad_input():
    assert rn.heyreach_connect_message({"eventType": "CONNECTION_REQUEST_ACCEPTED", "lead": {}}) is None
    assert rn.heyreach_connect_message({"message": "   "}) is None
    assert rn.heyreach_connect_message({}) is None
    assert rn.heyreach_connect_message(None) is None


# ── integration: the webhook end to end ──────────────────────────────────


@pytest.fixture
def client(tmp_path, monkeypatch):
    from auto_search.db.engagement_repository import EngagementJsonRepository
    from auto_search.db.repository import JsonFileRepository
    from auto_search.db.scoring_repository import ScoringJsonRepository
    for var in ("BASIC_AUTH_USER", "BASIC_AUTH_PASS", "DATABASE_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HEYREACH_WEBHOOK_SECRET", "s3cret")
    repo = JsonFileRepository(tmp_path / "s.json")
    repo.replace_abm_targets([{"name": "Acme Health", "domain": "acme.com"}])  # cross target
    eng = EngagementJsonRepository(tmp_path / "eng.json")
    monkeypatch.setattr(_app, "get_repository", lambda: repo)
    monkeypatch.setattr(_app, "get_scoring_repository", lambda: ScoringJsonRepository(tmp_path / "sc.json"))
    monkeypatch.setattr(_app, "get_engagement_repository", lambda: eng)
    with TestClient(_app.create_app()) as c:
        c._eng = eng
        yield c


def _post(client, body):
    return client.post("/api/campaigns/webhooks/heyreach?secret=s3cret", json=body)


def _connect_events(client):
    ev = client._eng.events_for_account("abm_acmehealth")
    return [e for e in ev if str(e.get("kind")).startswith("linkedin_connect")]


def test_webhook_connect_accept_scores_10_with_note_audit(client):
    r = _post(client, {"eventType": "CONNECTION_REQUEST_ACCEPTED",
                       "lead": {"profileUrl": "https://linkedin.com/in/jane",
                                "companyName": "Acme Health"},
                       "connectionMessage": "Hi Jane, would love to connect!",
                       "campaignId": "509139"})
    assert r.json().get("kind") == "linkedin_connect_message"
    ce = _connect_events(client)
    assert ce and ce[0]["kind"] == "linkedin_connect_message" and ce[0]["points"] == 10
    assert "connectNote" in ce[0]["raw"]         # audit trail stored


def test_webhook_connect_accept_scores_10_without_payload_note(client):
    # no note echoed in the payload -> still 10 (our connects always carry a note)
    r = _post(client, {"eventType": "CONNECTION_REQUEST_ACCEPTED",
                       "lead": {"profileUrl": "https://linkedin.com/in/joe",
                                "companyName": "Acme Health"},
                       "campaignId": "111"})
    assert r.json().get("kind") == "linkedin_connect_message"
    assert _connect_events(client)[0]["points"] == 10


def test_webhook_reply_still_scores_6(client):
    r = _post(client, {"eventType": "MESSAGE_REPLY_RECEIVED",
                       "lead": {"profileUrl": "https://linkedin.com/in/rae",
                                "companyName": "Acme Health"}})
    assert r.json().get("kind") == "linkedin_reply"


def test_webhook_untracked_company_dropped(client):
    r = _post(client, {"eventType": "CONNECTION_REQUEST_ACCEPTED",
                       "lead": {"profileUrl": "https://linkedin.com/in/x",
                                "companyName": "Some Random LLC"},
                       "campaignId": "509139"})
    assert r.json().get("matched") is False


def test_webhook_bad_secret_rejected(client):
    r = client.post("/api/campaigns/webhooks/heyreach?secret=wrong",
                    json={"eventType": "CONNECTION_REQUEST_ACCEPTED"})
    assert r.status_code == 403
