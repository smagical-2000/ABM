"""Engagement API (Milestone F) — GET /api/engagement[/{id}] + POST sync.

Forces JSON repos (no Postgres) like test_api.py; monkeypatches the engagement
sync so the POST never hits the network.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

from auto_search.db.engagement_repository import EngagementJsonRepository
from auto_search.db.repository import JsonFileRepository
from auto_search.db.scoring_repository import ScoringJsonRepository

_app = importlib.import_module("auto_search.api.app")


async def _noop_sync(**_kwargs):
    return {}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("BASIC_AUTH_USER", raising=False)
    monkeypatch.delenv("BASIC_AUTH_PASS", raising=False)
    monkeypatch.setattr(_app, "get_repository",
                        lambda: JsonFileRepository(tmp_path / "d.json"))
    monkeypatch.setattr(_app, "get_scoring_repository",
                        lambda: ScoringJsonRepository(tmp_path / "s.json"))

    eng = EngagementJsonRepository(path=str(tmp_path / "e.json"))
    eng.upsert_contact({"external_id": "1", "account_id": "acc_x", "company": "Acme",
                        "delivered": 10, "opened": 4, "replied": 1,
                        "matched_lists": ["abm"]})
    eng.add_event({"external_id": "email:reply:1", "kind": "reply", "channel": "email",
                   "points": 6, "contact_ext": "1", "account_id": "acc_x",
                   "occurred_at": "2026-06-10T00:00:00+00:00"})
    eng.add_event({"external_id": "email:meeting_booked:1", "kind": "meeting_booked",
                   "channel": "email", "points": 10, "contact_ext": "1",
                   "account_id": "acc_x", "occurred_at": "2026-06-11T00:00:00+00:00"})
    monkeypatch.setattr(_app, "get_engagement_repository", lambda: eng)
    monkeypatch.setattr(_app.engagement_sync_mod, "run_sync", _noop_sync)

    from auto_search.api.app import create_app
    with TestClient(create_app()) as c:
        yield c


def test_get_engagement_ranks_with_tier_and_rates(client):
    body = client.get("/api/engagement").json()
    accts = body["accounts"]
    assert accts and accts[0]["account_id"] == "acc_x"
    a = accts[0]
    assert a["score"] == 16 and a["tier"] == "Warm"      # reply 6 + meeting 10
    assert a["open_rate"] == 40 and a["reply_rate"] == 10  # 4/10, 1/10
    assert a["lists"] == ["abm"]


def test_get_engagement_account_detail(client):
    r = client.get("/api/engagement/acc_x").json()
    assert r["account"]["account_id"] == "acc_x"
    assert {e["kind"] for e in r["events"]} == {"reply", "meeting_booked"}
    assert len(r["contacts"]) == 1


def test_get_unknown_account_404(client):
    assert client.get("/api/engagement/nope").status_code == 404


def test_sync_endpoint_starts_background(client):
    res = client.post("/api/engagement/sync").json()
    assert res["started"] is True
