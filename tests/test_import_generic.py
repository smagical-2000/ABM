"""Generic accounts-list import through the API: per-row classification via the
high-confidence-only classifier (mocked here), segment breakdown in the preview,
unclassifiable rows dropped and reported, classified rows queued with the right
framework. The wizard's contract for non-Definitive CSVs (2026-07-08)."""

import importlib

import pytest
from fastapi.testclient import TestClient

from auto_search.engagement import classify as classify_mod

_app_module = importlib.import_module("auto_search.api.app")

_CSV = (
    "Account Name,Opportunity Name,Website Domain,Website\n"
    "Endeavor Health,Endeavor,endeavorhealth.org,https://endeavorhealth.org\n"
    "Humana,Humana - Enterprise,humana.com,humana.com\n"
    "Iowa Ortho,Iowa Ortho - NB,iowaortho.com,iowaortho.com\n"
    "Waud Capital Partners,Waud,,\n"
    "Guidehouse,Guidehouse | Partner,guidehouse.com,guidehouse.com\n"
)

# name -> what the classifier "knows" (Waud/Guidehouse are non-ICP / unsure)
_BUCKETS = {
    "Endeavor Health": ("health_system", "high"),
    "Humana": ("payer", "high"),
    "Iowa Ortho": ("specialty", "high"),
    "Waud Capital Partners": ("non_icp", "high"),
    "Guidehouse": ("specialty", "low"),          # a guess -> must be dropped
}


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

    async def fake_classify(name, domain=None):
        fw, conf = _BUCKETS.get(name, ("non_icp", "low"))
        return {"framework": fw, "confidence": conf, "reason": "test"}
    monkeypatch.setattr(classify_mod, "classify_account", fake_classify)

    with TestClient(_app_module.create_app()) as c:
        yield c


def test_preview_classifies_and_reports_breakdown(client):
    out = client.post("/api/scoring/import/preview", content=_CSV).json()
    assert out["schema_label"].startswith("Accounts list")
    assert out["segment"] == "mixed"
    assert out["segments"] == {"health_system": 1, "payer": 1, "specialty": 1}
    assert out["unclassified_count"] == 2
    assert set(out["unclassified"]) == {"Waud Capital Partners", "Guidehouse"}
    assert out["new_count"] == 3                     # only classified rows count


def test_commit_queues_classified_rows_with_frameworks(client):
    out = client.post("/api/scoring/import", content=_CSV,
                      headers={"x-import-filename": "sao_cohort.csv"}).json()
    assert out["imported"] == 3 and out["queued"] == 3
    assert out["unclassified_count"] == 2
    by = {a["name"]: a for a in out["accounts"]}
    assert by["Endeavor Health"]["segment"] == "health_system"
    assert by["Humana"]["segment"] == "payer"
    assert by["Iowa Ortho"]["segment"] == "specialty"
    assert all(a["state"] == "queued" for a in out["accounts"])   # never auto-scored
    assert all(a["framework"] for a in out["accounts"])           # rubric resolved
    # committed for real: the scored store now has them queued
    scored = client.get("/api/scored").json()
    rows = scored if isinstance(scored, list) else scored.get("accounts", [])
    names = {a["name"] for a in rows}
    assert {"Endeavor Health", "Humana", "Iowa Ortho"} <= names


def test_dhc_exports_do_not_hit_the_classifier(client, monkeypatch):
    async def boom(name, domain=None):
        raise AssertionError("classifier must not run for Definitive exports")
    monkeypatch.setattr(classify_mod, "classify_account", boom)
    hs = ("Hospital Name,Net Patient Revenue,State\n"
          "Beacon Health,\"$1,400,000,000\",IN\n")
    out = client.post("/api/scoring/import/preview", content=hs).json()
    assert out["segment"] == "health_system"
    assert "segments" not in out                     # generic-only fields absent
