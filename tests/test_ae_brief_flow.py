"""One-flow AE brief — the revert contract: AE_BRIEF_AUTO=0 must fully disable
the auto-dossier (score-only lookups, manual Generate still works), and the
commit response must tell the UI which mode it is in."""

import importlib

_app_module = importlib.import_module("auto_search.api.app")


def test_flag_default_on(monkeypatch):
    monkeypatch.delenv("AE_BRIEF_AUTO", raising=False)
    assert _app_module._ae_brief_auto() is True


def test_flag_zero_reverts(monkeypatch):
    monkeypatch.setenv("AE_BRIEF_AUTO", "0")
    assert _app_module._ae_brief_auto() is False


def test_commit_response_reports_brief_mode(tmp_path, monkeypatch):
    """The lookup commit tells the UI whether the brief will auto-open, and the
    flag flips it live (no redeploy) — the whole point of the revert switch."""
    from fastapi.testclient import TestClient

    from auto_search.db.repository import JsonFileRepository
    from auto_search.db.scoring_repository import ScoringJsonRepository

    monkeypatch.delenv("BASIC_AUTH_USER", raising=False)
    monkeypatch.delenv("BASIC_AUTH_PASS", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(_app_module, "get_repository",
                        lambda: JsonFileRepository(tmp_path / "store.json"))
    monkeypatch.setattr(_app_module, "get_scoring_repository",
                        lambda: ScoringJsonRepository(tmp_path / "scoring.json"))
    monkeypatch.setattr(_app_module, "_schedule_scoring",
                        lambda app, account_id, **kw: None)
    with TestClient(_app_module.create_app()) as c:
        monkeypatch.setenv("AE_BRIEF_AUTO", "0")
        off = c.post("/api/scoring/lookup/score", json={
            "name": "Brief Off Clinic", "domain": "off.com", "segment": "specialty"}).json()
        assert off["status"] == "scoring" and off["auto_brief"] is False

        monkeypatch.delenv("AE_BRIEF_AUTO", raising=False)
        on = c.post("/api/scoring/lookup/score", json={
            "name": "Brief On Clinic", "domain": "on.com", "segment": "specialty"}).json()
        assert on["status"] == "scoring" and on["auto_brief"] is True
