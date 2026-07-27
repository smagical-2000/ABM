"""MAR2-32 destroy-pass: adversarial interactions across the breaker, the audit
interlock, the staging gate, the identity heal, and the endpoint surface. Every
test here is a way the system could lie, leak, crash, or go silent — pinned so
none of them can come back quietly."""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from auto_search.db.engagement_repository import EngagementJsonRepository
from auto_search.engagement import audit, identity
from auto_search.engagement import notify as notify_mod

_app_module = importlib.import_module("auto_search.api.app")


class _Scoring:
    def __init__(self, rows):
        self._rows = rows

    def list_accounts(self):
        return self._rows


class _Discovery:
    def __init__(self, targets):
        self._t = targets

    def abm_targets(self):
        return self._t


@pytest.fixture
def client(tmp_path, monkeypatch):
    from auto_search.db.repository import JsonFileRepository
    from auto_search.db.scoring_repository import ScoringJsonRepository

    for var in ("BASIC_AUTH_USER", "BASIC_AUTH_PASS", "DATABASE_URL",
                "ENGAGEMENT_NOTIFY_SANE_MAX"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(_app_module, "get_repository",
                        lambda: JsonFileRepository(tmp_path / "s.json"))
    monkeypatch.setattr(_app_module, "get_scoring_repository",
                        lambda: ScoringJsonRepository(tmp_path / "sc.json"))
    monkeypatch.setattr(_app_module, "get_engagement_repository",
                        lambda: EngagementJsonRepository(tmp_path / "eng.json"))
    with TestClient(_app_module.create_app()) as c:
        yield c


def _seed_hot(repo, aid="abm_dueco", company="Due Co", when="2026-07-07T10:00:00+00:00"):
    """Matrix-true Hot (26 = 10 + 10 + 6). Stamps the heal marker afterwards —
    the ingest pipeline always heals after persisting (I5)."""
    for ext, kind, pts in ((f"m:{aid}", "meeting_booked", 10),
                           (f"b:{aid}", "high_intent_lead", 10),
                           (f"r:{aid}", "reply", 6)):
        repo.add_event({"source": "replyio", "external_id": ext, "channel": "email",
                        "kind": kind, "points": pts, "contact_ext": f"c:{aid}",
                        "company": company, "account_id": aid,
                        "occurred_at": when, "raw": {}})
    # Activation is ABM-only (2026-07-22): the board reads ABM membership off a
    # contact's matched_lists, so a due account needs one to pass the gate.
    repo.upsert_contact({"source": "replyio", "external_id": f"c:{aid}",
                         "company": company, "account_id": aid, "matched_lists": ["abm"]})
    _mark_healed(repo)


def _mark_healed(repo):
    import json
    from datetime import UTC, datetime
    repo.set_setting("identity_heal_last", json.dumps(
        {"at": datetime.now(UTC).isoformat(), "merged": 0, "manual": 0}))


def _capture_alerts(monkeypatch):
    from auto_search.ops import alerts as ops_alerts
    sent = []
    monkeypatch.setattr(ops_alerts, "should_alert", lambda *a, **k: True)
    monkeypatch.setattr(ops_alerts, "post_ops_alert",
                        lambda **kw: sent.append(kw) or True)
    return sent


# ── breaker × ceiling source × copy ───────────────────────────────────────


def test_ceiling_zero_declares_hold_all_and_names_accounts(client, monkeypatch):
    """The 2026-07-14 01:14 alert class: ceiling 0 must self-describe as a
    deliberate hold-all AND name what it held — never 'abnormal volume'."""
    repo = client.app.state.engagement_repo
    _seed_hot(repo)
    repo.set_setting("notify_sane_max", "0")
    sent = _capture_alerts(monkeypatch)
    out = client.post("/api/engagement/notify-changes?stage=live&dry_run=false").json()
    assert out["held"] is True and out["posted"] == 0
    assert out["sane_max"] == 0 and out["ceiling_source"] == "setting"
    assert "hold-all engaged" in sent[0]["title"]
    assert "Due Co" in sent[0]["detail"] or "abm_dueco" in sent[0]["detail"]


def test_ceiling_env_zero_reports_env_source(client, monkeypatch):
    repo = client.app.state.engagement_repo
    _seed_hot(repo)
    monkeypatch.setenv("ENGAGEMENT_NOTIFY_SANE_MAX", "0")
    _capture_alerts(monkeypatch)
    out = client.post("/api/engagement/notify-changes?stage=live&dry_run=false").json()
    assert out["held"] is True and out["ceiling_source"] == "env"


def test_ceiling_garbage_falls_back_to_default_25(client, monkeypatch):
    repo = client.app.state.engagement_repo
    _seed_hot(repo)
    repo.set_setting("notify_sane_max", "banana")
    calls = []
    monkeypatch.setattr(notify_mod, "activate_account",
                        lambda a, e, **kw: calls.append(1) or True)
    out = client.post("/api/engagement/notify-changes?stage=live&dry_run=false").json()
    assert out.get("held") is None and out["posted"] == 1   # 1 due ≤ 25


def test_ceiling_negative_is_hold_all(client, monkeypatch):
    repo = client.app.state.engagement_repo
    _seed_hot(repo)
    repo.set_setting("notify_sane_max", "-5")
    sent = _capture_alerts(monkeypatch)
    out = client.post("/api/engagement/notify-changes?stage=live&dry_run=false").json()
    assert out["held"] is True
    assert "hold-all engaged" in sent[0]["title"]


def test_small_hold_names_accounts_not_volume_language(client, monkeypatch):
    repo = client.app.state.engagement_repo
    _seed_hot(repo, "abm_a", "Alpha Co")
    _seed_hot(repo, "abm_b", "Beta Co")
    repo.set_setting("notify_sane_max", "1")
    sent = _capture_alerts(monkeypatch)
    out = client.post("/api/engagement/notify-changes?stage=live&dry_run=false").json()
    assert out["held"] is True
    assert "Abnormal" not in sent[0]["detail"]
    # names fall back to account ids here (no scored/abm registration in the
    # fixture) — the point is the HELD accounts are named, whatever the label
    assert "abm_a" in sent[0]["detail"] and "abm_b" in sent[0]["detail"]


# ── interlock beats everything ────────────────────────────────────────────


def test_audit_hold_wins_over_allow_burst(client, monkeypatch):
    """allow_burst overrides the VOLUME breaker, never the truth audit —
    a reviewed-large-batch flag must not ship from an inconsistent board."""
    repo = client.app.state.engagement_repo
    _seed_hot(repo)
    repo.add_event({"source": "replyio", "external_id": "poison", "channel": "email",
                    "kind": "click", "points": 9, "contact_ext": "c:x",
                    "company": "Due Co", "account_id": "abm_dueco",
                    "occurred_at": "2026-07-07T11:00:00+00:00", "raw": {}})
    calls = []
    monkeypatch.setattr(notify_mod, "activate_account",
                        lambda a, e, **kw: calls.append(1) or True)
    out = client.post(
        "/api/engagement/notify-changes?stage=live&allow_burst=true&dry_run=false").json()
    assert out.get("stage") == "audit" and out["posted"] == 0 and calls == []


def test_seed_is_held_on_red_board(client, monkeypatch):
    repo = client.app.state.engagement_repo
    _seed_hot(repo)
    repo.add_event({"source": "replyio", "external_id": "poison", "channel": "email",
                    "kind": "click", "points": 9, "contact_ext": "c:x",
                    "company": "Due Co", "account_id": "abm_dueco",
                    "occurred_at": "2026-07-07T11:00:00+00:00", "raw": {}})
    out = client.post("/api/engagement/notify-changes?seed=true").json()
    assert out.get("stage") == "audit"
    assert client.app.state.engagement_repo.get_setting("notified_tiers") in (
        None, "", "{}")   # a red board can never become the baseline


# ── heal: hostile identity shapes ─────────────────────────────────────────


def test_three_way_identity_is_manual_never_guessed(tmp_path):
    repo = EngagementJsonRepository(tmp_path / "e.json")
    scoring_repo = _Scoring([
        {"account_id": "csv_acme_health", "name": "Acme Health", "domain": "acme.com"},
        {"account_id": "acc_acmehealth", "name": "Acme Health", "domain": "acme.com"}])
    disc = _Discovery([{"name": "Acme Health", "domain": "acme.com"}])
    for aid in ("csv_acme_health", "acc_acmehealth", "abm_acmehealth"):
        repo.add_event({"source": "replyio", "external_id": f"e:{aid}",
                        "channel": "email", "kind": "click", "points": 1,
                        "contact_ext": aid, "company": "Acme Health",
                        "account_id": aid,
                        "occurred_at": "2026-07-01T00:00:00+00:00", "raw": {}})
    rep = identity.heal_identity_splits(repo, scoring_repo, disc)
    assert rep["merged"] == {} and rep["manual"][0]["why"] == "no single canonical id"
    assert len({r["account_id"] for r in repo.engaged_accounts()}) == 3


def test_unicode_and_case_variants_still_group(tmp_path):
    repo = EngagementJsonRepository(tmp_path / "e.json")
    scoring_repo = _Scoring([{"account_id": "csv_summa", "name": "SUMMA Health System!",
                              "domain": "summa.org"}])
    disc = _Discovery([{"name": "Summa   Health System", "domain": "summa.org"}])
    for aid in ("csv_summa", "abm_summahealthsystem"):
        repo.add_event({"source": "replyio", "external_id": f"e:{aid}",
                        "channel": "email", "kind": "click", "points": 1,
                        "contact_ext": aid, "company": "Summa",
                        "account_id": aid,
                        "occurred_at": "2026-07-01T00:00:00+00:00", "raw": {}})
    rep = identity.heal_identity_splits(repo, scoring_repo, disc)
    assert rep["merged"] == {"abm_summahealthsystem": "csv_summa"}


def test_activation_on_both_ids_keeps_earliest_claim(tmp_path):
    repo = EngagementJsonRepository(tmp_path / "e.json")
    scoring_repo = _Scoring([{"account_id": "csv_acme_health", "name": "Acme Health"}])
    disc = _Discovery([{"name": "Acme Health"}])
    for aid in ("csv_acme_health", "abm_acmehealth"):
        repo.add_event({"source": "replyio", "external_id": f"e:{aid}",
                        "channel": "email", "kind": "click", "points": 1,
                        "contact_ext": aid, "company": "Acme Health",
                        "account_id": aid,
                        "occurred_at": "2026-07-01T00:00:00+00:00", "raw": {}})
    repo.claim_activation("csv_acme_health", at="2026-07-01T00:00:00+00:00")
    repo.claim_activation("abm_acmehealth", at="2026-07-02T00:00:00+00:00")
    identity.heal_identity_splits(repo, scoring_repo, disc)
    assert repo.is_activated("csv_acme_health")
    assert not repo.is_activated("abm_acmehealth")
    assert len(repo.activated_account_ids()) == 1


def test_nameless_ids_never_merge_or_crash(tmp_path):
    repo = EngagementJsonRepository(tmp_path / "e.json")
    for aid in ("csv_mystery", "abm_mystery2"):
        repo.add_event({"source": "replyio", "external_id": f"e:{aid}",
                        "channel": "email", "kind": "click", "points": 1,
                        "contact_ext": aid, "company": None, "account_id": aid,
                        "occurred_at": "2026-07-01T00:00:00+00:00", "raw": {}})
    rep = identity.heal_identity_splits(repo, _Scoring([]), _Discovery([]))
    assert rep["merged"] == {}


# ── audit: hostile data shapes ────────────────────────────────────────────


def test_deprecated_kind_with_wrong_points_is_ignored(tmp_path):
    repo = EngagementJsonRepository(tmp_path / "e.json")
    repo.add_event({"source": "sfdc", "external_id": "old", "channel": "crm",
                    "kind": "sales_accepted_opportunity", "points": 99,
                    "contact_ext": "c", "company": "Acme Health",
                    "account_id": "csv_acme_health",
                    "occurred_at": "2026-05-01T00:00:00+00:00", "raw": {}})
    _mark_healed(repo)
    rep = audit.run_invariants(
        repo, _Scoring([{"account_id": "csv_acme_health", "name": "Acme Health"}]),
        _Discovery([]))
    assert rep["ok"] is True


def test_zero_point_open_never_moves_recompute_touch(tmp_path):
    repo = EngagementJsonRepository(tmp_path / "e.json")
    repo.add_event({"source": "replyio", "external_id": "r", "channel": "email",
                    "kind": "reply", "points": 6, "contact_ext": "c",
                    "company": "Acme Health", "account_id": "csv_acme_health",
                    "occurred_at": "2026-07-01T00:00:00+00:00", "raw": {}})
    repo.add_event({"source": "replyio", "external_id": "o", "channel": "email",
                    "kind": "open", "points": 0, "contact_ext": "c",
                    "company": "Acme Health", "account_id": "csv_acme_health",
                    "occurred_at": "2026-07-09T00:00:00+00:00", "raw": {}})
    _mark_healed(repo)
    rep = audit.run_invariants(
        repo, _Scoring([{"account_id": "csv_acme_health", "name": "Acme Health"}]),
        _Discovery([]))
    assert rep["ok"] is True     # both sides ignore the newer 0-pt open


def test_ghost_account_trips_I3(tmp_path):
    """Events exist, tile missing from the served view — the purest silent
    false negative (nothing downstream can ever see this account)."""
    repo = EngagementJsonRepository(tmp_path / "e.json")
    repo.add_event({"source": "replyio", "external_id": "r", "channel": "email",
                    "kind": "reply", "points": 6, "contact_ext": "c",
                    "company": "Ghost Co", "account_id": "csv_ghost",
                    "occurred_at": "2026-07-01T00:00:00+00:00", "raw": {}})
    rep = audit.run_invariants(repo, _Scoring([]), _Discovery([]), rows=[])
    assert any(v["code"] == "I3-ghost" for v in rep["violations"])


def test_audit_scales_to_thousands_of_events(tmp_path):
    repo = EngagementJsonRepository(tmp_path / "e.json")
    for i in range(2000):
        repo.add_event({"source": "replyio", "external_id": f"e{i}",
                        "channel": "email", "kind": "click", "points": 1,
                        "contact_ext": f"c{i % 40}", "company": f"Co {i % 40}",
                        "account_id": f"csv_co_{i % 40}",
                        "occurred_at": "2026-07-01T00:00:00+00:00", "raw": {}})
    import time
    _mark_healed(repo)
    t0 = time.monotonic()
    rep = audit.run_invariants(
        repo, _Scoring([{"account_id": f"csv_co_{i}", "name": f"Co {i}"}
                        for i in range(40)]), _Discovery([]))
    assert rep["ok"] and rep["stats"]["events_scanned"] == 2000
    assert time.monotonic() - t0 < 5.0


# ── endpoint fuzz ─────────────────────────────────────────────────────────


def test_negative_limit_posts_nothing_no_crash(client, monkeypatch):
    repo = client.app.state.engagement_repo
    _seed_hot(repo)
    calls = []
    monkeypatch.setattr(notify_mod, "activate_account",
                        lambda a, e, **kw: calls.append(1) or True)
    out = client.post(
        "/api/engagement/notify-changes?stage=live&limit=-1&dry_run=false").json()
    assert out["posted"] == 0 and calls == []


def test_non_integer_limit_is_422_not_500(client):
    r = client.post("/api/engagement/notify-changes?limit=lots")
    assert r.status_code == 422


def test_audit_and_heal_on_empty_store(client):
    rep = client.get("/api/engagement/audit").json()
    assert rep["ok"] is True and rep["stats"]["tiles"] == 0
    healed = client.post("/api/engagement/heal").json()
    assert healed.get("merged") == {}
