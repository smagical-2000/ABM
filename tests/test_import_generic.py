"""Generic accounts-list import through the API: per-row classification (mocked
here), segment breakdown in the preview, EVERY row imported — borderline rows
carry a Classification flag instead of being dropped (Sunny, 2026-07-08) — and
all rows queued with a resolved rubric. The wizard's contract for
non-Definitive CSVs."""

import importlib

import pytest
from fastapi.testclient import TestClient

from auto_search.engagement import classify as classify_mod
from auto_search.scoring import imports

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
    "Guidehouse": ("specialty", "low"),          # a guess -> imports FLAGGED
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
    # ALL rows import: non-ICP/low-confidence land in specialty as the
    # catch-all rubric, carrying a Classification flag
    assert out["segments"] == {"health_system": 1, "payer": 1, "specialty": 3}
    assert out["flagged_count"] == 2
    assert set(out["flagged"]) == {"Waud Capital Partners", "Guidehouse"}
    assert out["new_count"] == 5                     # every row counts


def test_commit_queues_classified_rows_with_frameworks(client):
    out = client.post("/api/scoring/import", content=_CSV,
                      headers={"x-import-filename": "sao_cohort.csv"}).json()
    assert out["imported"] == 5 and out["queued"] == 5
    assert out["flagged_count"] == 2
    by = {a["name"]: a for a in out["accounts"]}
    assert by["Endeavor Health"]["segment"] == "health_system"
    assert by["Humana"]["segment"] == "payer"
    assert by["Iowa Ortho"]["segment"] == "specialty"
    # flagged rows import too, on the catch-all rubric, with the note as a fact
    assert by["Waud Capital Partners"]["segment"] == "specialty"
    waud_note = by["Waud Capital Partners"]["firmographics"]["Classification"]
    assert "likely not ICP" in waud_note and "verify segment" in waud_note
    guide_note = by["Guidehouse"]["firmographics"]["Classification"]
    assert "low confidence" in guide_note
    # clean rows carry NO flag
    assert "Classification" not in by["Endeavor Health"]["firmographics"]
    assert all(a["state"] == "queued" for a in out["accounts"])   # never auto-scored
    assert all(a["framework"] for a in out["accounts"])           # rubric resolved
    # committed for real: the scored store now has them queued
    scored = client.get("/api/scored").json()
    rows = scored if isinstance(scored, list) else scored.get("accounts", [])
    names = {a["name"] for a in rows}
    assert {"Endeavor Health", "Humana", "Iowa Ortho",
            "Waud Capital Partners", "Guidehouse"} <= names


def test_dhc_exports_do_not_hit_the_classifier(client, monkeypatch):
    async def boom(name, domain=None):
        raise AssertionError("classifier must not run for Definitive exports")
    monkeypatch.setattr(classify_mod, "classify_account", boom)
    hs = ("Hospital Name,Net Patient Revenue,State\n"
          "Beacon Health,\"$1,400,000,000\",IN\n")
    out = client.post("/api/scoring/import/preview", content=hs).json()
    assert out["segment"] == "health_system"
    assert "segments" not in out                     # generic-only fields absent


# ── QA follow-ups (2026-07-08 independent QA pass) ────────────────────


def test_ragged_rows_do_not_crash_either_schema():
    """QA F1: an extra comma in a hand-edited row parks overflow cells in a
    LIST under DictReader's None restkey — that must be dropped, not .strip()'d
    (it 500'd the whole import). Same guard for generic and Definitive."""
    generic = ("Account Name,Website Domain\n"
               "Smith, Jones Ortho,sjortho.com\n"          # ragged: 3 cells
               "Clean Health,cleanhealth.org\n")
    res = imports.parse_csv(generic)
    assert res.schema_key == imports.GENERIC_KEY
    assert {a.name for a in res.accounts} == {"Smith", "Clean Health"}
    dhc = ("Hospital Name,Net Patient Revenue,State\n"
           "Beacon Health,\"$1,400,000,000\",IN,overflow-cell\n")
    res2 = imports.parse_csv(dhc)
    assert [a.name for a in res2.accounts] == ["Beacon Health"]


def test_generic_row_cap_rejects_oversized_lists():
    """QA F4: every generic row is classified inside one synchronous request —
    oversized lists are rejected up front with a clear message."""
    rows = "\n".join(f"Account {i},acct{i}.com" for i in range(imports.GENERIC_MAX_ROWS + 1))
    text = "Account Name,Website Domain\n" + rows + "\n"
    with pytest.raises(imports.ImportError_, match="capped"):
        imports.parse_csv(text)
    ok = "Account Name,Website Domain\n" + "\n".join(
        f"Account {i},acct{i}.com" for i in range(3)) + "\n"
    assert len(imports.parse_csv(ok).accounts) == 3


def test_classifier_error_flags_neutrally_and_reimport_counts_zero(client, monkeypatch):
    """QA F3+F4 (focused pass): an LLM outage must read 'classification
    unavailable', never 'likely not ICP'; and a full re-import that dedupes
    everything must report flagged_count 0, not re-claim old flags."""
    async def outage(name, domain=None):
        return {"framework": "non_icp", "confidence": "low", "reason": "classify error"}
    monkeypatch.setattr(classify_mod, "classify_account", outage)
    csv_text = "Account Name,Website Domain\nBeacon Health,beacon.org\n"
    first = client.post("/api/scoring/import", content=csv_text).json()
    assert first["imported"] == 1 and first["flagged_count"] == 1
    row = first["accounts"][0]
    note = row["firmographics"]["Classification"]
    assert "classification unavailable" in note
    assert "not ICP" not in note and "classify error" not in note
    again = client.post("/api/scoring/import", content=csv_text).json()
    assert again["imported"] == 0 and again["skipped_known"] == 1
    assert again["flagged_count"] == 0 and again["flagged"] == []


def test_payers_schema_routes_and_carries_lives():
    """Payers export (Account Name + Est. Lives Covered + HQ State) routes to the
    payer rubric (NOT generic), and the covered-lives number is carried as a
    known fact so the 200k+ payer gate scores on real size (Sunny, 2026-07-09)."""
    csv_text = (
        "Account Name,Est. Lives Covered,HQ State\n"
        "Blue Cross Blue Shield of Michigan,5400000,MI\n"
        "Health Alliance Plan,650000,MI\n"
    )
    res = imports.parse_csv(csv_text)
    assert res.schema_key == "payers"
    assert res.segment == "payer"
    assert res.schema_key != imports.GENERIC_KEY      # not the classify path
    a = res.accounts[0]
    assert a.name == "Blue Cross Blue Shield of Michigan"
    assert a.segment == "payer" and a.framework == "payer"
    # the sizing signal the rubric gates on is present in the scorer's facts
    assert a.firmographics.get("Estimated Lives Covered") == "5400000"
    assert a.firmographics.get("HQ State") == "MI"
