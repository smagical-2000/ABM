"""ABM target-list API - import, panel annotation, matches endpoint, filter.

Forces the JSON repo (no Postgres) by monkeypatching get_repository, mirroring
test_api.py. Seeds two qualified companies: one with a domain (matches the
target by domain -> confirmed) and one name-only (matches by name, no state
corroboration -> review).
"""

import importlib
import io
from datetime import UTC, datetime

import openpyxl
import pytest
from fastapi.testclient import TestClient

from auto_search.db.repository import JsonFileRepository
from auto_search.db.scoring_repository import ScoringJsonRepository
from auto_search.models import CompanyCandidate, QualificationResult, RawSignal
from auto_search.scoring.frameworks import framework_for_segment
from auto_search.scoring.models import Account

_app_module = importlib.import_module("auto_search.api.app")


def _candidate(key, name, *, domain=None):
    return CompanyCandidate(
        company_key=key, company_name=name,
        signals=[RawSignal(
            source="signalbase_leadership", source_external_id=f"{key}::1",
            signal_type="leadership_change", company_name_raw=name,
            observed_at=datetime(2026, 5, 1, tzinfo=UTC), signal_strength=0.9)],
        qualification=QualificationResult(
            qualified=True, confidence=0.9, reasoning="x",
            segment="health_system", domain=domain),
    )


def _seed_scored(store, name, *, domain=None, source="discovery"):
    """Put one account on the scoring board (state is irrelevant to ABM matching)."""
    ScoringJsonRepository(store).upsert_account(
        Account(
            account_id=f"acc_{source}_{name.lower().replace(' ', '')}",
            name=name, segment="health_system", source=source,
            framework=framework_for_segment("health_system").key, domain=domain,
        ),
        state="queued",
    )


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = tmp_path / "store.json"
    repo = JsonFileRepository(store)
    repo.save_candidate(_candidate("acmehealth", "Acme Health"))
    repo.save_candidate(_candidate("bryanhealth", "Bryan Health", domain="bryanhealth.com"))

    scoring_store = tmp_path / "scoring.json"
    _seed_scored(scoring_store, "Bryan Health", domain="bryanhealth.com")  # discovery + domain → confirmed
    _seed_scored(scoring_store, "Acme Health")                            # discovery + name-only → review
    _seed_scored(scoring_store, "Unrelated Clinic", source="csv")          # CSV import → never tagged

    monkeypatch.delenv("BASIC_AUTH_USER", raising=False)
    monkeypatch.delenv("BASIC_AUTH_PASS", raising=False)
    monkeypatch.setattr(_app_module, "get_repository", lambda: JsonFileRepository(store))
    monkeypatch.setattr(_app_module, "get_scoring_repository",
                        lambda: ScoringJsonRepository(scoring_store))
    monkeypatch.setattr(_app_module, "_schedule_scoring",
                        lambda app, account_id, **kw: None)
    from auto_search.api.app import create_app
    with TestClient(create_app()) as c:
        yield c


def _workbook() -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    ws = wb.create_sheet("Health Systems")
    ws.append(["Hospital Name", "State", "Website"])
    ws.append(["Acme Health", "NE", ""])               # name-only -> review
    ws.append(["Bryan Health", "NE", "bryanhealth.com"])  # domain -> confirmed
    ws.append(["Unrelated Clinic", "CA", ""])           # not in the panel
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_no_match_before_upload(client):
    panel = client.get("/api/panel").json()
    assert panel and all(c.get("abm_match") is None for c in panel)


def test_import_then_panel_is_annotated(client):
    r = client.post("/api/abm/import", content=_workbook())
    assert r.status_code == 200
    assert r.json()["stored"] == 3

    summary = client.get("/api/abm/summary").json()
    assert summary["total"] == 3
    assert summary["indexed"] == 3

    panel = {c["name"]: c for c in client.get("/api/panel").json()}
    assert panel["Bryan Health"]["abm_match"]["tier"] == "confirmed"
    assert panel["Bryan Health"]["abm_match"]["how"] == "domain"
    assert panel["Acme Health"]["abm_match"]["tier"] == "review"


def test_matches_endpoint_sorts_confirmed_first(client):
    client.post("/api/abm/import", content=_workbook())
    matches = client.get("/api/abm/matches").json()
    assert {m["name"] for m in matches} == {"Bryan Health", "Acme Health"}
    assert matches[0]["abm_match"]["tier"] == "confirmed"   # confirmed sorts first


def test_panel_abm_filter(client):
    client.post("/api/abm/import", content=_workbook())
    confirmed = client.get("/api/panel?abm=confirmed").json()
    assert [c["name"] for c in confirmed] == ["Bryan Health"]
    any_match = client.get("/api/panel?abm=match").json()
    assert {c["name"] for c in any_match} == {"Bryan Health", "Acme Health"}


def test_scored_board_has_no_match_before_upload(client):
    scored = client.get("/api/scored").json()
    assert scored and all(a.get("abm_match") is None for a in scored)


def test_scored_board_is_annotated_after_upload(client):
    client.post("/api/abm/import", content=_workbook())
    scored = {a["name"]: a for a in client.get("/api/scored").json()}
    # Domain-bearing scored account → confirmed via domain (location-independent).
    assert scored["Bryan Health"]["abm_match"]["tier"] == "confirmed"
    assert scored["Bryan Health"]["abm_match"]["how"] == "domain"
    # Name-only scored account → review (no signal geography to corroborate).
    assert scored["Acme Health"]["abm_match"]["tier"] == "review"


def test_scored_csv_import_is_never_tagged(client):
    # 'Unrelated Clinic' IS on the uploaded list, but it's a CSV import (from the
    # ABM sheet itself), so it's a target by definition — no badge.
    client.post("/api/abm/import", content=_workbook())
    scored = {a["name"]: a for a in client.get("/api/scored").json()}
    assert scored["Unrelated Clinic"]["abm_match"] is None


def test_import_rejects_empty_upload(client):
    assert client.post("/api/abm/import", content=b"").status_code == 400
