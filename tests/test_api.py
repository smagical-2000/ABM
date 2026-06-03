"""FastAPI app tests — routing, workflow, auth, static mount.

Forces the JSON repo (no Postgres needed) by monkeypatching get_repository,
so these run in CI with zero infra.
"""

import base64
import importlib
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from auto_search.db.repository import JsonFileRepository
from auto_search.models import CompanyCandidate, QualificationResult, RawSignal

# The module (not the package attribute `app`, which is the FastAPI instance).
_app_module = importlib.import_module("auto_search.api.app")


def _seed(store_path):
    repo = JsonFileRepository(store_path)
    repo.save_candidate(CompanyCandidate(
        company_key="acmehealth", company_name="Acme Health",
        signals=[RawSignal(
            source="signalbase_leadership", source_external_id="acme::1",
            signal_type="leadership_change", company_name_raw="Acme Health",
            observed_at=datetime(2026, 5, 1, tzinfo=UTC), signal_strength=0.9,
            payload={"new_role": "Chief Financial Officer"},
        )],
        qualification=QualificationResult(
            qualified=True, confidence=0.9, reasoning="community hospital",
            segment="health_system", evidence_url="https://acme.org/about",
        ),
    ))
    return repo


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = tmp_path / "store.json"
    _seed(store)
    monkeypatch.delenv("BASIC_AUTH_USER", raising=False)
    monkeypatch.delenv("BASIC_AUTH_PASS", raising=False)
    monkeypatch.setattr(_app_module, "get_repository",
                        lambda: JsonFileRepository(store))
    # Isolate the scoring store and neutralize the background scorer — these API
    # tests cover routing/workflow, not the (LLM) scoring pass.
    from auto_search.db.scoring_repository import ScoringJsonRepository
    monkeypatch.setattr(_app_module, "get_scoring_repository",
                        lambda: ScoringJsonRepository(tmp_path / "scoring.json"))
    monkeypatch.setattr(_app_module, "_schedule_scoring",
                        lambda app, account_id: None)
    from auto_search.api.app import create_app
    with TestClient(create_app()) as c:
        yield c


class TestReads:
    def test_health(self, client):
        assert client.get("/api/health").json() == {"ok": True}

    def test_stats(self, client):
        s = client.get("/api/stats").json()
        assert s["qualified"] == 1 and s["panel_pending"] == 1

    def test_panel_lists_seeded(self, client):
        rows = client.get("/api/panel").json()
        assert [r["name"] for r in rows] == ["Acme Health"]
        assert rows[0]["signals"][0]["summary"] == "Chief Financial Officer"

    def test_company_detail(self, client):
        r = client.get("/api/company/acmehealth").json()
        assert r["segment"] == "health_system"

    def test_company_404(self, client):
        assert client.get("/api/company/nope").status_code == 404


class TestWorkflow:
    def test_promote_then_gone_from_panel(self, client):
        r = client.post("/api/company/acmehealth/promote")
        assert r.status_code == 200
        # promote now creates a scoring account (was a stub id)
        assert r.json()["account_id"] == "acc_acmehealth"
        assert client.get("/api/panel").json() == []
        # and the account is now in the scoring phase
        scored = client.get("/api/scored").json()
        assert any(a["account_id"] == "acc_acmehealth" for a in scored)

    def test_promote_404(self, client):
        assert client.post("/api/company/nope/promote").status_code == 404

    def test_reject_requires_reason(self, client):
        # missing body → 422 validation
        assert client.post("/api/company/acmehealth/reject").status_code == 422
        ok = client.post("/api/company/acmehealth/reject", json={"reason": "too small"})
        assert ok.status_code == 200

    def test_defer(self, client):
        assert client.post("/api/company/acmehealth/defer").status_code == 200
        assert client.get("/api/panel").json() == []


class TestStaticMount:
    def test_serves_ui_index(self, client):
        # The static UI is mounted at / (serves index.html). 200 confirms mount.
        assert client.get("/").status_code == 200


class TestAuth:
    def test_gate_when_credentials_set(self, tmp_path, monkeypatch):
        store = tmp_path / "s.json"
        _seed(store)
        monkeypatch.setenv("BASIC_AUTH_USER", "u")
        monkeypatch.setenv("BASIC_AUTH_PASS", "p")
        monkeypatch.setattr(_app_module, "get_repository",
                            lambda: JsonFileRepository(store))
        from auto_search.api.app import create_app
        with TestClient(create_app()) as c:
            assert c.get("/api/stats").status_code == 401        # gated
            assert c.get("/api/health").status_code == 200       # exempt
            tok = base64.b64encode(b"u:p").decode()
            ok = c.get("/api/stats", headers={"Authorization": f"Basic {tok}"})
            assert ok.status_code == 200
            bad = base64.b64encode(b"u:wrong").decode()
            assert c.get("/api/stats",
                         headers={"Authorization": f"Basic {bad}"}).status_code == 401
