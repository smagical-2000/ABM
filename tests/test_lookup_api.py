"""AE one-off lookup endpoints — resolve + commit, on the JSON repos with the
scheduler stubbed and Exa/Claude patched. The contracts under test:
  - existing data always wins (and never spends),
  - commit is race-safe (an id mismatch can never create a twin),
  - commit re-kicks a parked/failed row instead of duplicating it,
  - only a confirmed 'new' account enqueues, source='ae', state='scoring'."""

import importlib
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from auto_search.clients.exa import ExaResult
from auto_search.db.repository import JsonFileRepository
from auto_search.db.scoring_repository import ScoringJsonRepository
from auto_search.models import CompanyCandidate, QualificationResult, RawSignal
from auto_search.scoring import lookup
from auto_search.scoring.frameworks import framework_for_segment
from auto_search.scoring.models import Account

_app_module = importlib.import_module("auto_search.api.app")


def _candidate(key, name):
    return CompanyCandidate(
        company_key=key, company_name=name,
        signals=[RawSignal(
            source="signalbase_leadership", source_external_id=f"{key}::1",
            signal_type="leadership_change", company_name_raw=name,
            observed_at=datetime(2026, 5, 1, tzinfo=UTC), signal_strength=0.9)],
        qualification=QualificationResult(
            qualified=True, confidence=0.9, reasoning="x", segment="specialty"),
    )


def _account(name, *, domain=None, state="scored", source="discovery"):
    return Account(
        account_id="acc_" + name.lower().replace(" ", ""), name=name,
        segment="specialty", source=source,
        framework=framework_for_segment("specialty").key, domain=domain,
    ), state


@pytest.fixture
def env(tmp_path, monkeypatch):
    store, scoring_store = tmp_path / "store.json", tmp_path / "scoring.json"
    repo = JsonFileRepository(store)
    repo.save_candidate(_candidate("orthoindy", "OrthoIndy"))
    srepo = ScoringJsonRepository(scoring_store)
    for a, state in (_account("Bryan Health", domain="bryanhealth.com"),
                     _account("Parked Clinic", domain="parked.com", state="queued")):
        srepo.upsert_account(a, state=state)

    monkeypatch.delenv("BASIC_AUTH_USER", raising=False)
    monkeypatch.delenv("BASIC_AUTH_PASS", raising=False)
    # Hermetic: without this the app boots the ENGAGEMENT repo onto the local
    # Postgres from .env (slow + nondeterministic engagement garnish in resolve).
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(_app_module, "get_repository", lambda: JsonFileRepository(store))
    monkeypatch.setattr(_app_module, "get_scoring_repository",
                        lambda: ScoringJsonRepository(scoring_store))
    scheduled = []
    monkeypatch.setattr(_app_module, "_schedule_scoring",
                        lambda app, account_id, **kw: scheduled.append(account_id))
    with TestClient(_app_module.create_app()) as c:
        yield c, scheduled


def _patch_web(monkeypatch, ident):
    monkeypatch.setattr(lookup.exa, "search", lambda *a, **k: [
        ExaResult(title="Ivy Rehab", url="https://ivyrehab.com/",
                  domain="ivyrehab.com", text="Outpatient PT network")])

    async def fake(_n, _d, _r):
        return ident, 0.01
    monkeypatch.setattr(lookup, "_identify", fake)


_IDENT = {"matched": True, "name": "Ivy Rehab Network", "domain": "ivyrehab.com",
          "segment": "specialty", "confidence": "high",
          "description": "Outpatient PT network.", "evidence_url": "https://ivyrehab.com/"}


# ── resolve ───────────────────────────────────────────────────────────


def test_resolve_requires_name(env):
    c, _ = env
    assert c.post("/api/scoring/lookup", json={}).status_code == 422


def test_resolve_existing_scored_short_circuits(env, monkeypatch):
    c, _ = env

    def boom(*a, **k):
        raise AssertionError("must not search for an existing account")
    monkeypatch.setattr(lookup.exa, "search", boom)
    out = c.post("/api/scoring/lookup",
                 json={"name": "bryan health", "website": "x.com"}).json()
    assert out["status"] == "already_scored"
    assert out["account"]["account_id"] == "acc_bryanhealth"


def test_resolve_live_discovery(env):
    c, _ = env
    out = c.post("/api/scoring/lookup", json={"name": "OrthoIndy"}).json()
    assert out["status"] == "in_discovery"
    assert out["company"]["key"] == "orthoindy"
    assert out["company"]["signals"] == 1


def test_resolve_new_company(env, monkeypatch):
    c, _ = env
    _patch_web(monkeypatch, _IDENT)
    out = c.post("/api/scoring/lookup",
                 json={"name": "ivy rehab", "website": "ivyrehab.com"}).json()
    assert out["status"] == "new"
    assert out["resolved"]["name"] == "Ivy Rehab Network"
    assert out["engagement"] is None       # garnish present, empty here


# ── commit ────────────────────────────────────────────────────────────


def test_commit_new_account_scores(env):
    c, scheduled = env
    out = c.post("/api/scoring/lookup/score", json={
        "name": "Ivy Rehab Network", "domain": "ivyrehab.com",
        "segment": "specialty", "description": "Outpatient PT network."}).json()
    assert out["status"] == "scoring"
    assert out["account_id"] == "acc_ivyrehabnetwork"
    assert out["account"]["source"] == "ae"
    assert scheduled == ["acc_ivyrehabnetwork"]
    board = c.get("/api/scored").json()
    assert any(a["account_id"] == "acc_ivyrehabnetwork" for a in board)


def test_commit_duplicate_returns_existing_no_twin(env):
    c, scheduled = env
    out = c.post("/api/scoring/lookup/score", json={
        "name": "Bryan Health System",           # different name, same domain
        "domain": "bryanhealth.com", "segment": "specialty"}).json()
    assert out["status"] == "already_scored"
    assert out["account_id"] == "acc_bryanhealth"
    assert scheduled == []                       # no paid work
    names = [a["account_id"] for a in c.get("/api/scored").json()]
    assert names.count("acc_bryanhealth") == 1
    assert "acc_bryanhealthsystem" not in names  # the twin that must not exist


def test_commit_rekicks_parked_row(env):
    c, scheduled = env
    out = c.post("/api/scoring/lookup/score", json={
        "name": "Parked Clinic", "domain": "parked.com", "segment": "specialty"}).json()
    assert out["status"] == "scoring"
    assert out["rekicked"] is True
    assert scheduled == ["acc_parkedclinic"]     # the EXISTING id, no twin


def test_commit_parked_row_over_budget_says_so(env, monkeypatch):
    """No headroom + a parked row: the response must say budget_blocked (an
    'already_scored' here would hide that the click did nothing)."""
    c, scheduled = env
    monkeypatch.setattr(_app_module.budget_guard, "remaining", lambda _s: 0.0)
    out = c.post("/api/scoring/lookup/score", json={
        "name": "Parked Clinic", "domain": "parked.com", "segment": "specialty"}).json()
    assert out["status"] == "queued"
    assert out["budget_blocked"] is True
    assert scheduled == []                       # nothing spent


def test_commit_discovery_company_redirects_to_promote(env):
    c, scheduled = env
    out = c.post("/api/scoring/lookup/score", json={
        "name": "OrthoIndy", "domain": "orthoindy.com", "segment": "specialty"}).json()
    assert out["status"] == "in_discovery"
    assert out["company_key"] == "orthoindy"
    assert scheduled == []


def test_commit_validates_segment(env):
    c, _ = env
    r = c.post("/api/scoring/lookup/score", json={
        "name": "X", "domain": "x.com", "segment": "non_icp"})
    assert r.status_code == 422


# ── engagement garnish (pure helper, shaped-board rows) ───────────────


def test_engagement_context_matches_by_name_and_domain():
    rows = [{"account_id": "abm_x", "name": "Ivy Rehab Network",
             "domain": "ivyrehab.com", "tier": "Warm", "score": 17,
             "last_touch": "2026-07-01"}]
    by_name = _app_module._lookup_engagement_context(rows, "ivy rehab network, llc", None)
    assert by_name and by_name["tier"] == "Warm" and by_name["heat"] == 17
    by_domain = _app_module._lookup_engagement_context(
        rows, "totally different", "https://www.ivyrehab.com/about")
    assert by_domain and by_domain["tier"] == "Warm"
    assert _app_module._lookup_engagement_context(rows, "someone else", "x.com") is None
    assert _app_module._lookup_engagement_context([], "ivy", None) is None
