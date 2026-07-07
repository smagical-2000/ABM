"""The notifier staging gate — the trust contract: while notify_stage=test,
cards go ONLY to the private test channel ([TEST], plain names) and the ledger
is untouched, so the SAME accounts remain due for the explicit live push after
human verification. An explicit stage=live param overrides the setting."""

import importlib
import json

import pytest
from fastapi.testclient import TestClient

from auto_search.engagement import notify as notify_mod

_app_module = importlib.import_module("auto_search.api.app")


@pytest.fixture
def client(tmp_path, monkeypatch):
    from auto_search.db.engagement_repository import EngagementJsonRepository
    from auto_search.db.repository import JsonFileRepository
    from auto_search.db.scoring_repository import ScoringJsonRepository

    for var in ("BASIC_AUTH_USER", "BASIC_AUTH_PASS", "DATABASE_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(_app_module, "get_repository",
                        lambda: JsonFileRepository(tmp_path / "s.json"))
    monkeypatch.setattr(_app_module, "get_scoring_repository",
                        lambda: ScoringJsonRepository(tmp_path / "sc.json"))
    monkeypatch.setattr(_app_module, "get_engagement_repository",
                        lambda: EngagementJsonRepository(tmp_path / "eng.json"))
    with TestClient(_app_module.create_app()) as c:
        yield c


def _seed_due_account(app):
    """One Hot account with an event, not in the ledger -> due to notify."""
    repo = app.state.engagement_repo
    repo.upsert_contact({"source": "replyio", "external_id": "c1",
                         "email": "a@dueco.com", "email_domain": "dueco.com",
                         "company": "Due Co", "company_key": "dueco",
                         "account_id": "abm_dueco", "match_tier": "domain",
                         "matched_lists": ["abm"], "delivered": 1})
    repo.add_event({"source": "replyio", "external_id": "e:reply:c1",
                       "channel": "email", "kind": "meeting_booked", "points": 21,
                       "contact_ext": "c1", "company": "Due Co",
                       "account_id": "abm_dueco",
                       "occurred_at": "2026-07-07T10:00:00+00:00"})


def test_staged_send_posts_test_only_and_keeps_account_due(client, monkeypatch):
    app = client.app
    _seed_due_account(app)
    app.state.engagement_repo.set_setting("notify_stage", "test")
    calls = []
    monkeypatch.setattr(notify_mod, "activate_account",
                        lambda a, e, **kw: calls.append(kw) or True)

    out = client.post("/api/engagement/notify-changes").json()
    assert out["stage"] == "test" and out["posted"] == 1
    assert calls[0]["test"] is True          # [TEST] card
    assert calls[0]["webhook"] is None       # falls back to the PRIVATE webhook
    # ledger untouched -> still due for the real push
    assert app.state.engagement_repo.get_setting("notified_tiers") in (None, "{}", "")
    again = client.post("/api/engagement/notify-changes?dry_run=true").json()
    assert again["due"] == 1                 # the account is STILL queued


def test_explicit_live_param_overrides_and_marks_ledger(client, monkeypatch):
    app = client.app
    _seed_due_account(app)
    app.state.engagement_repo.set_setting("notify_stage", "test")
    monkeypatch.setattr(notify_mod, "activate_account", lambda a, e, **kw: True)

    out = client.post("/api/engagement/notify-changes?stage=live").json()
    assert out["stage"] == "live" and out["posted"] == 1
    led = json.loads(app.state.engagement_repo.get_setting("notified_tiers"))
    assert led["abm_dueco"]["tier"].lower() == "hot"
    after = client.post("/api/engagement/notify-changes?dry_run=true").json()
    assert after["due"] == 0                 # consumed by the live push


def test_stage_setting_endpoints(client):
    assert client.get("/api/engagement/settings/notify-stage").json()["stage"] == "live"
    assert client.post("/api/engagement/settings/notify-stage",
                       json={"stage": "test"}).json()["stage"] == "test"
    assert client.get("/api/engagement/settings/notify-stage").json()["stage"] == "test"
    assert client.post("/api/engagement/settings/notify-stage",
                       json={"stage": "bogus"}).status_code == 422


def test_zero_point_touches_never_advance_last_touch(client):
    """An email OPEN (0 pts) newer than the last scored touch must NOT move
    last_touch — otherwise every open would phantom re-alert a Hot account."""
    app = client.app
    _seed_due_account(app)
    app.state.engagement_repo.add_event({
        "source": "replyio", "external_id": "e:open:c1", "channel": "email",
        "kind": "open", "points": 0, "contact_ext": "c1", "company": "Due Co",
        "account_id": "abm_dueco", "occurred_at": "2026-07-08T09:00:00+00:00"})
    rows = app.state.engagement_repo.engaged_accounts()
    row = next(r for r in rows if r["account_id"] == "abm_dueco")
    assert row["last_touch"] == "2026-07-07T10:00:00+00:00"   # the scored touch, not the open
