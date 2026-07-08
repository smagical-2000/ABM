"""Styled Excel export: pure workbook builder (colors, wrapping, summary) and
the endpoint (ordered ids in -> streamed .xlsx out). The honesty contract:
every cell is data the account already carries; empty stays empty."""

import importlib
import io

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from auto_search.scoring import export_xlsx

_app_module = importlib.import_module("auto_search.api.app")


def _account(name="Beacon Health", fit="High", **over):
    a = {
        "account_id": "csv_" + name.lower().replace(" ", ""),
        "name": name, "domain": "beacon.org", "segment": "health_system",
        "state": "scored", "tier_label": fit, "total": 26, "max_total": 30,
        "cost_usd": 0.12, "scored_at": "2026-07-08T17:00:00+00:00",
        "import_label": "SAO cohort", "recommendation": "Strong fit; lead with prior-auth.",
        "qa": {"status": "verified", "notes": "Revenue confirmed."},
        "dimensions": [
            {"key": "firmographic", "label": "Firmographic", "score": 10, "max": 12},
            {"key": "technographic", "label": "Technographic", "score": 5, "max": 7},
            {"key": "intent", "label": "Business Priorities & Intent", "score": 9, "max": 11,
             "summary": "Hiring 3 RCM roles (Jun 2026); new CFO (May 2026)."},
        ],
        "dossier": {"intent_signals": [
            {"signal": "RCM hiring wave", "score": 8, "detail": "3 postings in June"}]},
        "discovery_signals": [{"signal_type": "job_posting", "summary": "3 Coder jobs"}],
        "firmographics": {"Net Patient Revenue": "$1.4B"},
    }
    a.update(over)
    return a


def test_workbook_rows_styling_and_summary():
    flagged = _account("Vendor Co", fit="Not a fit", total=2,
                       firmographics={"Classification": "auto-classified specialty (likely not ICP)"},
                       qa={"status": "discrepancy", "notes": "misclassified"},
                       dossier=None, discovery_signals=[], dimensions=[])
    wb = export_xlsx.build_workbook([_account(), flagged])
    ws = wb["Scored accounts"]
    head = [c.value for c in ws[1]]
    assert head[:5] == ["Account", "Domain", "Segment", "Fit", "Score"]
    assert "Intent evidence (researched)" in head and "Deep research signals" in head
    # row 2: evidence + dossier text land verbatim
    row2 = {h: ws.cell(row=2, column=i + 1).value for i, h in enumerate(head)}
    assert row2["Score"] == "26/30"
    assert "Hiring 3 RCM roles" in row2["Intent evidence (researched)"]
    assert "RCM hiring wave (8/10): 3 postings in June" == row2["Deep research signals"]
    assert row2["Discovery signals"] == "job_posting: 3 Coder jobs"
    # fit + QA cells carry their band fills; empty evidence stays empty
    assert ws.cell(row=2, column=4).fill.fgColor.rgb == "FFD1FAE5"      # High -> emerald
    assert ws.cell(row=3, column=4).fill.fgColor.rgb == "FFFEE2E2"      # Not a fit -> rose
    row3 = {h: ws.cell(row=3, column=i + 1).value for i, h in enumerate(head)}
    assert (row3["Intent evidence (researched)"] or "") == ""            # honesty: no data, no text
    assert ws.freeze_panes == "A2" and ws.auto_filter.ref.startswith("A1:")
    # summary sheet counts
    s = wb["Summary"]
    text = " ".join(str(c.value) for r in s.iter_rows() for c in r if c.value)
    assert "High 1" in text.replace("\n", " ") or ("High" in text and "1" in text)
    assert "Flagged (auto-classified segment): 1" in text
    assert "Accounts: 2" in text


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


def test_endpoint_streams_ordered_workbook(client):
    from datetime import UTC, datetime

    from auto_search.scoring.models import Account, Dimension, ScoreResult

    repo = client.app.state.scoring_repo
    for n, aid in (("Alpha Health", "csv_alphahealth"), ("Beta Health", "csv_betahealth")):
        repo.upsert_account(Account(
            account_id=aid, name=n, segment="health_system",
            framework="health_system", source="csv", domain="x.org"), state="queued")
        repo.save_score(aid, ScoreResult(
            account_id=aid, framework="health_system", framework_version="v",
            dimensions=[Dimension(key="intent", label="Intent", score=7, max=11,
                                  summary="Hiring wave (Jun 2026).")],
            total=21, max_total=30, tier_band="medium", tier_label="Medium",
            cost_usd=0.1, scored_at=datetime.now(UTC).isoformat()))
    # ids ordered Beta-first: the file must respect caller order (the UI's sort)
    ids = ["csv_betahealth", "csv_alphahealth"]
    r = client.post("/api/scoring/export.xlsx", json={"account_ids": ids})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats")
    wb = load_workbook(io.BytesIO(r.content))
    ws = wb["Scored accounts"]
    assert ws.cell(row=2, column=1).value == "Beta Health"
    assert ws.cell(row=3, column=1).value == "Alpha Health"
    assert client.post("/api/scoring/export.xlsx", json={}).status_code == 422
    assert client.post("/api/scoring/export.xlsx",
                       json={"account_ids": ["nope"]}).status_code == 404
