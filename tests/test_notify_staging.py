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
    # Hot via MATRIX-TRUE points (10+10+6=26): the audit interlock (MAR2-32)
    # holds any board whose stored points diverge from the canonical matrix,
    # so fixtures must seed like production ingests, not shortcut totals.
    for ext, kind, pts in (("e:meet:c1", "meeting_booked", 10),
                           ("e:bofu:c1", "high_intent_lead", 10),
                           ("e:reply:c1", "reply", 6)):
        repo.add_event({"source": "replyio", "external_id": ext,
                        "channel": "email", "kind": kind, "points": pts,
                        "contact_ext": "c1", "company": "Due Co",
                        "account_id": "abm_dueco",
                        "occurred_at": "2026-07-07T10:00:00+00:00"})
    # ingest pipeline always heals after persisting (I5, MAR2-32 v2)
    import json as _json
    from datetime import UTC as _UTC
    from datetime import datetime as _dt
    repo.set_setting("identity_heal_last", _json.dumps(
        {"at": _dt.now(_UTC).isoformat(), "merged": 0, "manual": 0}))


def test_staged_send_posts_test_only_and_keeps_account_due(client, monkeypatch):
    app = client.app
    _seed_due_account(app)
    app.state.engagement_repo.set_setting("notify_stage", "test")
    calls = []
    monkeypatch.setattr(notify_mod, "activate_account",
                        lambda a, e, **kw: calls.append(kw) or True)

    out = client.post("/api/engagement/notify-changes?dry_run=false").json()
    assert out["stage"] == "test" and out["posted"] == 1
    assert calls[0]["test"] is True          # [TEST] card
    assert calls[0]["webhook"] is None       # falls back to the PRIVATE webhook
    # REAL ledger untouched -> still due for the live push...
    assert app.state.engagement_repo.get_setting("notified_tiers") in (None, "{}", "")
    # ...but the TEST ledger has memory (2026-07-10 flood fix): a repeat
    # auto-trigger in test stage must NOT re-post the same card.
    import json as _json
    tled = _json.loads(app.state.engagement_repo.get_setting("notified_tiers_test") or "{}")
    assert tled and next(iter(tled.values()))["tier"].lower() == "hot"
    again = client.post("/api/engagement/notify-changes?dry_run=true").json()
    assert again["due"] == 0                 # test channel: seen once, silent now
    repeat = client.post("/api/engagement/notify-changes?dry_run=false").json()
    assert repeat["posted"] == 0             # no re-flood
    # the LIVE view still owes the card: explicit stage=live sees it as due
    live_view = client.post(
        "/api/engagement/notify-changes?dry_run=true&stage=live").json()
    assert live_view["due"] == 1


def test_manual_activate_is_coerced_to_test_while_staged(client, monkeypatch):
    """Sunny's "if I hit Activate, does it go to TEST or main?" guarantee: while
    notify_stage=test, a bare manual POST /api/engagement/<id>/activate (no
    {"test": true} in the body) is COERCED to a test post by the staging gate in
    app.py — test=True ([TEST] card on the private webhook), webhook=None (never
    the live AE/SDR channel), no paid enrichment, and no activation claim, so
    the account is still activatable for real after go-live."""
    app = client.app
    _seed_due_account(app)
    app.state.engagement_repo.set_setting("notify_stage", "test")
    calls = []
    monkeypatch.setattr(notify_mod, "activate_account",
                        lambda a, e, **kw: calls.append(kw) or True)

    out = client.post("/api/engagement/abm_dueco/activate", json={}).json()
    assert out["posted"] is True
    assert calls and calls[0]["test"] is True    # is_test coerced true by the gate
    assert calls[0]["webhook"] is None           # private test webhook, not a channel
    assert calls[0]["dms"] == []                 # test posts never spend Apollo credits
    # no claim taken -> not marked activated; the real send is still owed
    assert app.state.engagement_repo.activated_account_ids() == set()


def test_explicit_live_param_overrides_and_marks_ledger(client, monkeypatch):
    app = client.app
    _seed_due_account(app)
    app.state.engagement_repo.set_setting("notify_stage", "test")
    monkeypatch.setattr(notify_mod, "activate_account", lambda a, e, **kw: True)

    out = client.post("/api/engagement/notify-changes?stage=live&dry_run=false").json()
    assert out["stage"] == "live" and out["posted"] == 1
    led = json.loads(app.state.engagement_repo.get_setting("notified_tiers"))
    # MAR2-31: entries are company-keyed; this fixture's board can't resolve a
    # display name (name falls back to the id), so ledger_key correctly falls
    # back to the ACCOUNT ID rather than normalizing an id into a garbage key.
    assert led["abm_dueco"]["tier"].lower() == "hot"
    assert led["abm_dueco"]["account_id"] == "abm_dueco"
    after = client.post("/api/engagement/notify-changes?dry_run=true").json()
    assert after["due"] == 0                 # consumed by the live push


def test_bare_post_is_a_dry_run_not_a_live_send(client, monkeypatch):
    """SAFE BY DEFAULT (COO QA 2026-07-27): a parameterless authenticated POST
    used to fire REAL cards at the AE/SDR routing — one curl slip or a script
    bug reached live sales channels. Sending is now an explicit act
    (dry_run=false); every scheduled caller passes it."""
    app = client.app
    _seed_due_account(app)
    calls = []
    monkeypatch.setattr(notify_mod, "activate_account",
                        lambda a, e, **kw: calls.append(1) or True)

    out = client.post("/api/engagement/notify-changes?stage=live").json()
    assert out["due"] == 1 and out["posted"] == 0 and out["dry_run"] is True
    assert calls == []                       # nothing reached Slack
    assert app.state.engagement_repo.get_setting("notified_tiers") in (None, "", "{}")
    # ...and the explicit opt-in still sends, unchanged
    out2 = client.post("/api/engagement/notify-changes?stage=live&dry_run=false").json()
    assert out2["posted"] == 1 and calls


def test_seed_is_still_audit_gated_without_an_explicit_dry_run(client):
    """The dry_run default flip must NOT open a hole in the MAR2-32 interlock:
    seeding WRITES the baseline, so a bare `seed=true` stays held on a red
    board exactly as before."""
    app = client.app
    _seed_due_account(app)
    app.state.engagement_repo.add_event({
        "source": "replyio", "external_id": "e:click:c1", "channel": "email",
        "kind": "click", "points": 3, "contact_ext": "c1", "company": "Due Co",
        "account_id": "abm_dueco", "occurred_at": "2026-07-07T11:00:00+00:00"})
    out = client.post("/api/engagement/notify-changes?seed=true").json()
    assert out.get("stage") == "audit" and out.get("held") is True
    assert app.state.engagement_repo.get_setting("notified_tiers") in (None, "", "{}")


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


def test_circuit_breaker_holds_and_allow_burst_overrides(client, monkeypatch):
    """MAR2-31 breaker (QA panel: previously zero tests on the only guard
    between an identity burst and a channel flood): due above the ceiling ->
    ZERO sends, held response, ledger untouched; allow_burst=true overrides."""
    import auto_search.engagement.notify as notify_mod
    app = client.app
    _seed_due_account(app)
    app.state.engagement_repo.set_setting("notify_sane_max", "0")   # 1 due > 0 trips it
    calls = []
    monkeypatch.setattr(notify_mod, "activate_account",
                        lambda a, e, **kw: calls.append(1) or True)
    out = client.post("/api/engagement/notify-changes?stage=live&dry_run=false").json()
    assert out.get("held") is True and out["posted"] == 0 and calls == []
    assert app.state.engagement_repo.get_setting("notified_tiers") in (None, "{}", "")
    # deliberate override sends
    out2 = client.post(
        "/api/engagement/notify-changes?stage=live&allow_burst=true&dry_run=false").json()
    assert out2.get("held") is None and out2["posted"] == 1 and calls


def test_endpoint_applies_activation_cutoff(client, monkeypatch):
    """The endpoint wires the activation_cutoff setting into the pure gate:
    with a cutoff after the account's only touch, nothing is due."""
    app = client.app
    _seed_due_account(app)                       # touch 2026-07-07
    app.state.engagement_repo.set_setting("activation_cutoff", "2026-07-08")
    out = client.post("/api/engagement/notify-changes?dry_run=true").json()
    assert out["due"] == 0
    app.state.engagement_repo.set_setting("activation_cutoff", "2026-07-01")
    out2 = client.post("/api/engagement/notify-changes?dry_run=true").json()
    assert out2["due"] == 1


def test_audit_endpoint_routes_and_reports(client):
    """MAR2-32 trust monitor over HTTP. Pins the ROUTE, not just the function —
    the first deploy registered /api/engagement/audit after the /{account_id}
    catch-all and FastAPI swallowed 'audit' as an account id (404)."""
    _seed_due_account(client.app)
    rep = client.get("/api/engagement/audit")
    assert rep.status_code == 200
    body = rep.json()
    assert body["ok"] is True and body["violations"] == []
    assert body["stats"]["tiles"] == 1

    # heal endpoint routes too, and is a no-op on a clean store
    healed = client.post("/api/engagement/heal")
    assert healed.status_code == 200
    assert healed.json().get("merged") == {}


def test_interlock_holds_send_when_audit_red(client, monkeypatch):
    """A board that fails its own audit must HOLD sends (fail-safe) while a
    dry_run still passes through for inspection."""
    import auto_search.engagement.notify as notify_mod
    app = client.app
    _seed_due_account(app)
    # poison one event's points so I2 trips (stored 3 != canonical click 1)
    app.state.engagement_repo.add_event({
        "source": "replyio", "external_id": "e:click:c1", "channel": "email",
        "kind": "click", "points": 3, "contact_ext": "c1", "company": "Due Co",
        "account_id": "abm_dueco", "occurred_at": "2026-07-07T11:00:00+00:00"})
    calls = []
    monkeypatch.setattr(notify_mod, "activate_account",
                        lambda a, e, **kw: calls.append(1) or True)
    out = client.post("/api/engagement/notify-changes?stage=live&dry_run=false").json()
    assert out.get("held") is True and out.get("stage") == "audit"
    assert out["posted"] == 0 and calls == []
    assert any(v["code"] == "I2-points" for v in out["violations"])
    # dry run is the inspection path: passes through, still posts nothing
    dry = client.post("/api/engagement/notify-changes?dry_run=true").json()
    assert dry.get("stage") != "audit" and calls == []
