"""Tests for the scoring logic — no live LLM calls.

Pure logic (frameworks, clamp, imports, QA tier-change) is tested directly; the
engine's LLM call is monkeypatched so parsing/clamping/tiering is deterministic.
"""

import pytest

from auto_search.scoring import engine, frameworks, imports
from auto_search.scoring.frameworks import FRAMEWORKS, resolve_tier
from auto_search.scoring.models import (
    Account,
    Dimension,
    QACorrection,
    QAResult,
    ScoreResult,
)
from auto_search.scoring.qa import mark_tier_changing

# ── frameworks ────────────────────────────────────────────────────────


class TestFrameworks:
    def test_segment_resolution(self):
        assert frameworks.framework_for_segment("payer").key == "payer"
        assert frameworks.framework_for_segment(None).key == "specialty"

    def test_tier_bands(self):
        hs = FRAMEWORKS["health_system"]
        assert resolve_tier(hs, 25).label == "Tier 1"   # 22-27
        assert resolve_tier(hs, 16).label == "Tier 2"   # 16-21
        assert resolve_tier(hs, 11).label == "Tier 3"   # 10-15 (matches MUSC 11/27)
        assert resolve_tier(hs, 9).label == "Tier 4"    # < 10

    def test_health_system_auto_tier_4_when_npr_zero(self):
        hs = FRAMEWORKS["health_system"]
        band = resolve_tier(hs, 23, [{"key": "npr", "score": 0}])
        assert band.band == "out" and band.label == "Tier 4"

    def test_public_shape(self):
        pub = frameworks.all_frameworks_public()
        assert set(pub) == {"health_system", "specialty", "payer"}
        assert pub["health_system"]["max_total"] == 27
        assert len(pub["health_system"]["dimensions"]) == 6
        assert pub["specialty"]["max_total"] == 30

    def test_health_system_pillar_rollup(self):
        # Per the rubric: Technographic = EMR + Tech Readiness; Business Intent =
        # Competitors + Pain + Leadership.
        pillars = {p["key"]: p["dims"] for p in
                   frameworks.all_frameworks_public()["health_system"]["pillars"]}
        assert pillars["firmographic"] == ["npr"]
        assert pillars["technographic"] == ["emr", "ai_readiness"]
        assert pillars["intent"] == ["competitor", "pain", "leadership"]


# ── models ────────────────────────────────────────────────────────────


def test_clamp_caps_dimensions_and_recomputes_total():
    r = ScoreResult(
        account_id="x", framework="health_system", framework_version="hs-2026.2",
        max_total=27, total=999, tier_band="low", tier_label="",
        dimensions=[
            Dimension(key="ai_readiness", label="AI", score=3, max=2),  # over ceiling
            Dimension(key="leadership", label="Lead", score=1, max=1),
        ],
    ).clamp()
    assert r.dimensions[0].score == 2.0
    assert r.total == 3


# ── engine parsing (LLM monkeypatched) ────────────────────────────────


@pytest.mark.asyncio
async def test_score_account_parses_clamps_and_tiers(monkeypatch):
    payload = """{
      "dimensions": [
        {"key": "npr", "score": 10, "summary": "NPR ~$1.4B"},
        {"key": "emr", "score": 5, "summary": "MEDITECH"},
        {"key": "competitor", "score": 3, "summary": "none"},
        {"key": "pain", "score": 5, "summary": "denials up"},
        {"key": "ai_readiness", "score": 9, "summary": "over ceiling on purpose"},
        {"key": "leadership", "score": 1, "summary": "new CFO"}
      ],
      "recommendation": "Strong fit."
    }"""

    async def fake_call(**kwargs):
        return object()
    monkeypatch.setattr(engine.llm, "call_with_web_search", fake_call)
    monkeypatch.setattr(engine.llm, "extract_text", lambda r: payload)
    monkeypatch.setattr(engine.llm, "extract_web_searches", lambda r: [])

    acc = Account(account_id="a", name="Beacon Health", segment="health_system",
                  framework="health_system", source="csv",
                  firmographics={"Net Patient Revenue": "$1.4B"})
    res = await engine.score_account(acc)

    assert res.framework == "health_system" and res.max_total == 27
    assert {d.key for d in res.dimensions} == {
        "npr", "emr", "competitor", "pain", "ai_readiness", "leadership"}
    ai = next(d for d in res.dimensions if d.key == "ai_readiness")
    assert ai.score == 2.0                       # clamped from 9 to /2 ceiling
    assert res.total == 26 and res.tier_label == "Tier 1"
    assert res.recommendation == "Strong fit."


@pytest.mark.asyncio
async def test_score_account_fills_missing_dimension_as_unknown(monkeypatch):
    payload = '{"dimensions": [{"key": "firmographic", "score": 8}], "recommendation": ""}'

    async def fake_call(**kwargs):
        return object()
    monkeypatch.setattr(engine.llm, "call_with_web_search", fake_call)
    monkeypatch.setattr(engine.llm, "extract_text", lambda r: payload)
    monkeypatch.setattr(engine.llm, "extract_web_searches", lambda r: [])

    acc = Account(account_id="a", name="X", segment="specialty",
                  framework="specialty", source="discovery")
    res = await engine.score_account(acc)
    tech = next(d for d in res.dimensions if d.key == "technographic")
    assert tech.score == 0.0 and "unknown" in tech.flags


@pytest.mark.asyncio
async def test_score_account_raises_on_non_json(monkeypatch):
    async def fake_call(**kwargs):
        return object()
    monkeypatch.setattr(engine.llm, "call_with_web_search", fake_call)
    monkeypatch.setattr(engine.llm, "extract_text", lambda r: "sorry, no JSON here")
    with pytest.raises(engine.ScoringError):
        await engine.score_account(Account(account_id="a", name="X", segment="payer",
                                           framework="payer", source="csv"))


# ── QA tier-change (deterministic) ────────────────────────────────────


def test_mark_tier_changing_detects_a_dropped_tier():
    hs = FRAMEWORKS["health_system"]
    score = ScoreResult(
        account_id="rv", framework="health_system", framework_version="hs-2026.2",
        max_total=27, total=19, tier_band="medium", tier_label="Tier 2",
        dimensions=[
            Dimension(key="npr", label="Net Patient Revenue", score=6, max=10),
            Dimension(key="emr", label="EMR Compatibility", score=5, max=5),
            Dimension(key="competitor", label="Competitor Landscape", score=2, max=4),
            Dimension(key="pain", label="Pain Point Signals", score=4, max=5),
            Dimension(key="ai_readiness", label="AI & Tech Readiness", score=1, max=2),
            Dimension(key="leadership", label="Leadership Changes", score=1, max=1),
        ],
    )
    qa = QAResult(status="discrepancy", corrections=[
        QACorrection(dimension="npr", claimed="6/10 (~$2.0B)",
                     found="2/10 (~$2.9B)", corrected_score=2),
    ])
    mark_tier_changing(hs, score, qa)
    assert qa.tier_changing is True            # 19 -> 15 crosses Tier 2 -> Tier 3

    qa_minor = QAResult(status="discrepancy", corrections=[
        QACorrection(dimension="competitor", claimed="2/4", found="3/4",
                     corrected_score=3),
    ])
    mark_tier_changing(hs, score, qa_minor)
    assert qa_minor.tier_changing is False     # 19 -> 20, still Tier 2


# ── CSV import ────────────────────────────────────────────────────────

_HS_CSV = (
    "Hospital Name,Firm Type,Net Patient Revenue,"
    "Electronic Health/Medical Record - Inpatient,# of Staffed Beds,State\n"
    "Beacon Health,Health System,\"$1,400,000,000\",MEDITECH,310,IN\n"
    "Cedar Falls Medical Center,Health System,\"$880,000,000\",MEDITECH,190,IA\n"
    ",Health System,\"$0\",Epic,0,XX\n"  # no name -> skipped
)

_PG_CSV = (
    "Physician Group Name,Website,Number of Locations,# of Physicians,"
    "Ambulatory EMR,Main Specialty,State\n"
    "Gulf Coast Anesthesia,https://www.gulfcoastanes.com/,5,18,eClinicalWorks,"
    "Anesthesiology,TX\n"
)


class TestImports:
    def test_detect_and_parse_health_systems(self):
        res = imports.parse_csv(_HS_CSV)
        assert res.schema_key == "health_systems" and res.segment == "health_system"
        assert len(res.accounts) == 2 and res.skipped == 1
        a = res.accounts[0]
        assert a.name == "Beacon Health" and a.framework == "health_system"
        assert a.firmographics["Net Patient Revenue"] == "$1,400,000,000"
        assert a.firmographics["EHR Inpatient"] == "MEDITECH"
        assert a.account_id.startswith("csv_")

    def test_detect_and_parse_physician_groups(self):
        res = imports.parse_csv(_PG_CSV)
        assert res.schema_key == "physician_groups" and res.segment == "specialty"
        a = res.accounts[0]
        assert a.domain == "gulfcoastanes.com"          # stripped from URL
        assert a.firmographics["Physicians"] == "18"
        assert a.firmographics["Specialty"] == "Anesthesiology"

    def test_unknown_schema_raises(self):
        with pytest.raises(imports.ImportError_):
            imports.parse_csv("Foo,Bar\n1,2\n")

    def test_mapping_lists_known_and_unmatched(self):
        res = imports.parse_csv(_HS_CSV)
        mapped_cols = {m.col for m in res.mapping}
        assert "Hospital Name" in mapped_cols and "Net Patient Revenue" in mapped_cols
        assert "Firm Type" in res.unmatched_columns


# ── service (engine + QA monkeypatched) ───────────────────────────────


async def _fake_hs_score(account):
    return ScoreResult(
        account_id=account.account_id, framework="health_system",
        framework_version="hs-2026.2", max_total=27, total=0,
        tier_band="x", tier_label="x", recommendation="ok",
        dimensions=[Dimension(key=k, label=k, score=s, max=m) for k, s, m in [
            ("npr", 10, 10), ("emr", 5, 5), ("competitor", 3, 4),
            ("pain", 4, 5), ("ai_readiness", 1, 2), ("leadership", 1, 1)]],
    ).clamp()


@pytest.mark.asyncio
async def test_service_enqueue_score_persist(monkeypatch, tmp_path):
    from auto_search.db.scoring_repository import ScoringJsonRepository
    from auto_search.scoring import service as svc_mod
    from auto_search.scoring.service import ScoringService

    svc = ScoringService(ScoringJsonRepository(path=str(tmp_path / "s.json")))
    row = svc.enqueue_discovery({
        "company_key": "beaconhealth", "name": "Beacon Health",
        "segment": "health_system", "domain": "beaconhealth.org",
        "approximate_employees": 4200,
        "signals": [{"signal_type": "leadership_change", "summary": "New CFO"}],
    }, state="scoring")
    assert row["account_id"] == "acc_beaconhealth" and row["state"] == "scoring"

    async def fake_qa(account, score, fw):
        return QAResult(status="verified", notes="ok")
    monkeypatch.setattr(svc_mod.engine, "score_account", _fake_hs_score)
    monkeypatch.setattr(svc_mod.qa, "qa_account", fake_qa)

    scored = await svc.run_scoring("acc_beaconhealth")
    assert scored["state"] == "scored" and scored["total"] == 24
    assert scored["tier"]["label"] == "Tier 1"        # re-resolved at save time
    assert len(scored["dimensions"]) == 6 and scored["qa"]["status"] == "verified"
    assert scored["source"] == "discovery"
    assert svc.active() == []


@pytest.mark.asyncio
async def test_service_marks_error_on_failure(monkeypatch, tmp_path):
    from auto_search.db.scoring_repository import ScoringJsonRepository
    from auto_search.scoring import service as svc_mod
    from auto_search.scoring.engine import ScoringError
    from auto_search.scoring.service import ScoringService

    svc = ScoringService(ScoringJsonRepository(path=str(tmp_path / "s.json")))
    svc.enqueue_discovery({"company_key": "x", "name": "X", "segment": "payer"},
                          state="scoring")

    async def boom(account):
        raise ScoringError("LLM down")
    monkeypatch.setattr(svc_mod.engine, "score_account", boom)

    out = await svc.run_scoring("acc_x")
    assert out["state"] == "error" and "LLM down" in (out["error"] or "")
