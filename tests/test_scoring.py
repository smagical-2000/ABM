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


def test_apply_qa_corrections_updates_total_and_tier():
    from auto_search.scoring.qa import apply_qa_corrections

    hs = FRAMEWORKS["health_system"]
    score = ScoreResult(
        account_id="rv", framework="health_system", framework_version="v",
        max_total=27, total=24, tier_band="high", tier_label="Tier 1",
        dimensions=[
            Dimension(key="npr", label="Net Patient Revenue", score=10, max=10),
            Dimension(key="emr", label="EMR", score=5, max=5),
            Dimension(key="competitor", label="Competitor", score=4, max=4),
            Dimension(key="pain", label="Pain", score=3, max=5),
            Dimension(key="ai_readiness", label="AI", score=1, max=2),
            Dimension(key="leadership", label="Leadership", score=1, max=1),
        ],
    )
    qa = QAResult(status="discrepancy", corrections=[
        QACorrection(dimension="npr", claimed="10/10", found="far smaller", corrected_score=3),
    ])
    apply_qa_corrections(score, qa, hs)
    assert qa.applied is True and qa.analyst_total == 24
    assert score.total == 17                       # 24 - 7
    assert score.tier_label == "Tier 2"            # 17 lands in 16-21
    assert qa.tier_changing is True
    assert next(d for d in qa.analyst_dimensions if d.key == "npr").score == 10  # snapshot kept
    assert next(d for d in score.dimensions if d.key == "npr").score == 3        # official corrected


def test_correction_without_corrected_score_does_not_change_total():
    from auto_search.scoring.qa import apply_qa_corrections

    hs = FRAMEWORKS["health_system"]
    score = ScoreResult(
        account_id="x", framework="health_system", framework_version="v",
        max_total=27, total=24, tier_band="high", tier_label="Tier 1",
        dimensions=[Dimension(key="npr", label="NPR", score=10, max=10),
                    Dimension(key="emr", label="EMR", score=5, max=5)],
    )
    qa = QAResult(status="discrepancy", corrections=[
        QACorrection(dimension="npr", claimed="10/10", found="unsure", corrected_score=None),
    ])
    apply_qa_corrections(score, qa, hs)
    assert qa.applied is False and score.total == 24   # nothing actionable to apply


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


async def _fake_hs_score(account, prior=None):
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

    async def fake_qa(account, score, fw, *, depth="full"):
        assert depth == "full"                         # Tier 1 earns the full pass
        return QAResult(status="verified", notes="ok"), 0.012
    monkeypatch.setattr(svc_mod.engine, "score_account", _fake_hs_score)
    monkeypatch.setattr(svc_mod.qa, "qa_account", fake_qa)

    scored = await svc.run_scoring("acc_beaconhealth")
    assert scored["state"] == "scored" and scored["total"] == 24
    assert scored["cost_usd"] == 0.012                 # QA cost recorded
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

    async def boom(account, prior=None):
        raise ScoringError("LLM down")
    monkeypatch.setattr(svc_mod.engine, "score_account", boom)

    out = await svc.run_scoring("acc_x")
    assert out["state"] == "error" and "LLM down" in (out["error"] or "")


def test_repo_queued_and_cost_summary(tmp_path):
    """Parked 'queued' accounts cost nothing and are excluded from 'active';
    cost_summary aggregates measured spend against the monthly budget."""
    from datetime import UTC, datetime

    from auto_search.db.scoring_repository import ScoringJsonRepository

    repo = ScoringJsonRepository(path=str(tmp_path / "s.json"))
    repo.upsert_account(Account(
        account_id="csv_a", name="A", segment="specialty",
        framework="specialty", source="csv"), state="queued")
    repo.upsert_account(Account(
        account_id="csv_b", name="B", segment="specialty",
        framework="specialty", source="csv"), state="queued")

    assert {r["account_id"] for r in repo.queued()} == {"csv_a", "csv_b"}
    assert repo.active() == []                          # queued is parked, not active
    assert repo.get("csv_a")["elapsed_seconds"] is None  # no live clock when parked

    score = ScoreResult(
        account_id="csv_a", framework="specialty", framework_version="v",
        dimensions=[Dimension(key="k", label="k", score=5, max=10)],
        total=5, max_total=10, tier_band="medium", tier_label="Medium Fit",
        cost_usd=0.21, scored_at=datetime.now(UTC).isoformat())
    repo.save_score("csv_a", score)

    s = repo.cost_summary()
    assert s["scored_count"] == 1 and s["queued_count"] == 1
    assert s["total_cost"] == 0.21 and s["month_cost"] == 0.21
    assert s["monthly_budget"] == 200.0
    assert s["budget_remaining"] == round(200.0 - 0.21, 2)
    assert s["avg_cost"] == 0.21
    assert s["csv_avg_cost"] == 0.21                    # CSV-only average for the import estimate


def test_repo_recovers_orphaned_scoring(tmp_path):
    """A score whose background task died (e.g. a service restart) must not tick
    'scoring' forever: it is swept back to the queue, re-scoreable on demand."""
    from datetime import UTC, datetime, timedelta

    from auto_search.db.scoring_repository import ScoringJsonRepository

    repo = ScoringJsonRepository(path=str(tmp_path / "s.json"))
    for aid in ("acc_a", "acc_b"):
        repo.upsert_account(Account(
            account_id=aid, name=aid.upper(), segment="payer",
            framework="payer", source="discovery"), state="scoring")
    # acc_a just started; acc_b has been 'scoring' for an hour (orphaned).
    repo._store["acc_b"]["updated_at"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    repo._flush()

    # Threshold sweep returns only the stalled one to the queue.
    assert repo.recover_orphaned_scoring(1800) == 1
    assert repo.get("acc_b")["state"] == "queued" and repo.get("acc_b")["phase"] is None
    assert repo.get("acc_a")["state"] == "scoring"
    # Boot sweep (default 0) returns everything still in-flight to the queue.
    assert repo.recover_orphaned_scoring() == 1
    assert repo.get("acc_a")["state"] == "queued"
    assert repo.recover_orphaned_scoring() == 0          # idempotent once cleared


@pytest.mark.asyncio
async def test_csv_high_fit_runs_qa(monkeypatch, tmp_path):
    """High-fit CSV imports now get independent QA too (CEO accuracy); the QA
    prompt is told the CSV facts are authoritative, so it checks judgement."""
    from auto_search.db.scoring_repository import ScoringJsonRepository
    from auto_search.scoring import service as svc_mod
    from auto_search.scoring.service import ScoringService

    svc = ScoringService(ScoringJsonRepository(path=str(tmp_path / "s.json")))
    svc.enqueue_csv([Account(
        account_id="csv_x", name="X Health", segment="health_system",
        framework="health_system", source="csv",
        firmographics={"Net Patient Revenue": "$1.4B"})], state="scoring")

    depth_used = {}

    async def fake_qa(account, score, fw, *, depth="full"):
        depth_used["depth"] = depth
        return QAResult(status="verified", notes="checks out"), 0.05
    monkeypatch.setattr(svc_mod.engine, "score_account", _fake_hs_score)  # -> Tier 1
    monkeypatch.setattr(svc_mod.qa, "qa_account", fake_qa)

    scored = await svc.run_scoring("csv_x")
    assert depth_used["depth"] == "full"              # high fit CSV runs full QA
    assert scored["qa"]["status"] == "verified"
    assert scored["state"] == "scored"


@pytest.mark.asyncio
async def test_csv_qa_disabled_by_env(monkeypatch, tmp_path):
    """SCORING_QA_CSV=0 keeps the old cost-saving skip for CSV imports."""
    from auto_search.db.scoring_repository import ScoringJsonRepository
    from auto_search.scoring import service as svc_mod
    from auto_search.scoring.service import ScoringService

    monkeypatch.setenv("SCORING_QA_CSV", "0")
    svc = ScoringService(ScoringJsonRepository(path=str(tmp_path / "s.json")))
    svc.enqueue_csv([Account(account_id="csv_x", name="X", segment="health_system",
                             framework="health_system", source="csv")], state="scoring")

    called = False

    async def fake_qa(*a, **k):
        nonlocal called
        called = True
        return QAResult(status="verified"), 0.0
    monkeypatch.setattr(svc_mod.engine, "score_account", _fake_hs_score)
    monkeypatch.setattr(svc_mod.qa, "qa_account", fake_qa)

    scored = await svc.run_scoring("csv_x")
    assert called is False and scored["qa"]["status"] == "skipped"


def test_discovery_known_facts_carry():
    """A promoted company's qualification research rides into scoring as known
    facts, so the scorer does not re-research firmographics (the #1 cost cut)."""
    from auto_search.scoring.service import _account_from_discovery

    acc = _account_from_discovery({
        "company_key": "unitypoint", "name": "UnityPoint Health",
        "segment": "health_system", "company_type": "provider",
        "sub_segment": "IDN", "reasoning": "Large IDN on Epic; strong RCM fit.",
        "evidence_url": "https://unitypoint.org/about", "domain": "unitypoint.org",
        "approximate_employees": 32000,
        "signals": [{"signal_type": "leadership_change", "summary": "New CFO"}],
    })
    f = acc.firmographics
    assert f["Company type"] == "provider" and f["Sub-segment"] == "IDN"
    assert "Epic" in f["Discovery qualification"]
    assert f["Evidence URL"].startswith("https://")
    assert acc.discovery_signals[0]["summary"] == "New CFO"


@pytest.mark.asyncio
async def test_service_qa_depth_by_tier(monkeypatch, tmp_path):
    """QA spend follows fit: high earns the full pass, medium a focused one, low
    is skipped (marked 'skipped', not 'verified')."""
    from auto_search.db.scoring_repository import ScoringJsonRepository
    from auto_search.scoring import service as svc_mod
    from auto_search.scoring.service import ScoringService

    svc = ScoringService(ScoringJsonRepository(path=str(tmp_path / "s.json")))
    depths: list[str] = []

    async def fake_qa(account, score, fw, *, depth="full"):
        depths.append(depth)
        return QAResult(status="verified", notes="ok"), 0.02
    monkeypatch.setattr(svc_mod.qa, "qa_account", fake_qa)

    def hs_score(dims):
        async def _s(account, prior=None):
            return ScoreResult(
                account_id=account.account_id, framework="health_system",
                framework_version="v", max_total=27, total=0,
                tier_band="x", tier_label="x",
                dimensions=[Dimension(key=k, label=k, score=s, max=m)
                            for k, s, m in dims]).clamp()
        return _s

    async def score_one(key, dims):
        svc.enqueue_discovery({"company_key": key, "name": key,
                               "segment": "health_system"})
        monkeypatch.setattr(svc_mod.engine, "score_account", hs_score(dims))
        return await svc.run_scoring("acc_" + key)

    hi = await score_one("hi", [("npr", 10, 10), ("emr", 5, 5), ("competitor", 4, 4),
                                ("pain", 5, 5), ("ai_readiness", 2, 2), ("leadership", 1, 1)])   # 27 high
    med = await score_one("med", [("npr", 10, 10), ("emr", 5, 5), ("competitor", 1, 4),
                                  ("pain", 1, 5), ("ai_readiness", 1, 2), ("leadership", 0, 1)])  # 18 medium
    lo = await score_one("lo", [("npr", 10, 10), ("emr", 0, 5), ("competitor", 1, 4),
                                ("pain", 1, 5), ("ai_readiness", 0, 2), ("leadership", 0, 1)])    # 12 low

    assert depths == ["full", "light"]                 # low never calls QA
    assert hi["qa"]["status"] == "verified"
    assert med["qa"]["status"] == "verified"
    assert lo["qa"]["status"] == "skipped"
    assert "skipped" in lo["qa"]["notes"].lower()


def test_repo_reset_to_queued(tmp_path):
    """Reset clears a scored account back to a parked 'queued' one, non-destructively."""
    from datetime import UTC, datetime

    from auto_search.db.scoring_repository import ScoringJsonRepository

    repo = ScoringJsonRepository(path=str(tmp_path / "s.json"))
    repo.upsert_account(Account(account_id="a", name="A", segment="payer",
                                framework="payer", source="discovery"), state="queued")
    repo.save_score("a", ScoreResult(
        account_id="a", framework="payer", framework_version="v",
        dimensions=[Dimension(key="k", label="k", score=5, max=10)],
        total=5, max_total=10, tier_band="medium", tier_label="Medium Fit",
        cost_usd=0.2, scored_at=datetime.now(UTC).isoformat()))
    assert repo.get("a")["state"] == "scored"

    assert repo.reset_to_queued() == 1
    g = repo.get("a")
    assert g["state"] == "queued" and g["total"] is None
    assert g["tier"] is None and g["cost_usd"] == 0.0
    assert repo.reset_to_queued() == 0                  # already clean


def test_repo_import_labels(tmp_path):
    """Imported accounts carry the batch label; import_labels aggregates it for
    the filter. Discovery accounts have no label."""
    from auto_search.db.scoring_repository import ScoringJsonRepository

    repo = ScoringJsonRepository(path=str(tmp_path / "s.json"))
    label = "accounts.csv · Jun 04, 09:00"
    for i in range(2):
        repo.upsert_account(Account(
            account_id=f"csv_{i}", name=f"C{i}", segment="specialty",
            framework="specialty", source="csv"), state="queued", import_label=label)
    repo.upsert_account(Account(account_id="disc", name="D", segment="payer",
                                framework="payer", source="discovery"), state="queued")

    labels = repo.import_labels()
    assert len(labels) == 1 and labels[0] == {"label": label, "count": 2}
    assert repo.get("csv_0")["import_label"] == label
    assert repo.get("disc")["import_label"] is None


# ── dossier (landing-page deep research) ──────────────────────────────


def test_dossier_parse_shapes_and_guards():
    from auto_search.scoring import dossier as dmod

    d = dmod._parse({
        "firmographic_profile": [
            {"label": "Revenue", "value": "$145M", "confidence": "likely"},
            {"value": "no label"},                 # dropped (no label)
        ],
        "services": [{"label": "Core", "value": "Primary care"}],
        "intent_signals": [{"signal": "Epic go-live", "detail": "x", "score": 12}],  # clamps
        "entry_strategy": {"timing": "HIGH - now", "primary_angles": ["a", "b"],
                           "cautions": ["c"], "deal_size": "$0.4M-$1M"},
        "rcm_complexity": [{"label": "EHR System", "value": "Epic"}],
        "recent_news": [{"headline": "Epic go-live", "detail": "x", "date": "Sept 2025"}],
        "pain_points": ["denials", "  "],          # blank dropped
        "messaging_angles": ["Congrats on the Epic cutover"],
    })
    assert len(d.firmographic_profile) == 1
    assert d.firmographic_profile[0].confidence == "likely"
    assert d.intent_signals[0].score == 10                 # clamped to ceiling
    assert d.decision_makers == []                         # set from Apollo, not _parse
    assert d.entry_strategy.timing.startswith("HIGH") and d.entry_strategy.primary_angles == ["a", "b"]
    assert d.pain_points == ["denials"]
    assert d.messaging_angles == ["Congrats on the Epic cutover"]


def test_dossier_merge_people_uses_apollo_names():
    """Names + titles come verbatim from Apollo; the model supplies notes by
    order. A missing note degrades to empty, never a wrong name."""
    from auto_search.scoring import dossier as dmod

    people = [
        {"name": "Caroline Bosi", "title": "Revenue Cycle Manager",
         "linkedin": "http://linkedin.com/in/caroline"},
        {"name": "Jane Roe", "title": "CFO", "linkedin": ""},
    ]
    merged = dmod._merge_people(people, ["Owns A/R; primary buyer."])
    assert [m.contact for m in merged] == ["Caroline Bosi", "Jane Roe"]
    assert merged[0].role == "Revenue Cycle Manager"
    assert merged[0].notes == "Owns A/R; primary buyer."
    assert merged[0].linkedin.endswith("caroline")
    assert merged[1].notes == ""                            # no note -> blank, name intact


@pytest.mark.asyncio
async def test_apollo_no_key_or_domain(monkeypatch):
    from auto_search.scoring import apollo

    monkeypatch.delenv("APOLLO_API_KEY", raising=False)
    assert await apollo.decision_makers("avancecare.com") == []   # no key
    monkeypatch.setenv("APOLLO_API_KEY", "k")
    assert await apollo.decision_makers(None) == []               # no domain


def test_apollo_shape_prefers_full_name_and_dedups():
    """Enriched full name wins; a failed enrichment falls back to first name +
    title; duplicate names are dropped. Emails/phones are never carried."""
    from auto_search.scoring import apollo

    found = [
        {"id": "1", "first_name": "Caroline", "title": "RCM Mgr"},
        {"id": "2", "first_name": "Jane", "title": "CFO"},
        {"id": "3", "first_name": "Dup", "title": "X"},
    ]
    enriched = [
        {"name": "Caroline Bosi", "title": "Revenue Cycle Manager",
         "linkedin_url": "http://li/c", "email": "x@y.com"},
        None,                                              # failed -> fallback
        {"name": "Caroline Bosi", "title": "Dup"},         # duplicate -> dropped
    ]
    out = apollo._shape(found, enriched)
    assert [p["name"] for p in out] == ["Caroline Bosi", "Jane"]
    assert out[0]["title"] == "Revenue Cycle Manager"
    assert out[0]["linkedin"].endswith("/c")
    assert out[1]["title"] == "CFO" and out[1]["linkedin"] == ""
    assert all("email" not in p and "phone" not in p for p in out)


@pytest.mark.asyncio
async def test_service_generate_dossier(monkeypatch, tmp_path):
    """generate_dossier runs the engine on a scored account, stores the result,
    flips state to 'ready', and counts the cost toward the budget."""
    from auto_search.db.scoring_repository import ScoringJsonRepository
    from auto_search.scoring import service as svc_mod
    from auto_search.scoring.models import Dossier
    from auto_search.scoring.service import ScoringService

    repo = ScoringJsonRepository(path=str(tmp_path / "s.json"))
    svc = ScoringService(repo)
    repo.upsert_account(Account(account_id="acc_x", name="X Group", segment="specialty",
                                framework="specialty", source="discovery"), state="queued")
    repo.save_score("acc_x", ScoreResult(
        account_id="acc_x", framework="specialty", framework_version="v",
        dimensions=[Dimension(key="k", label="K", score=8, max=10, summary="s")],
        total=8, max_total=10, tier_band="high", tier_label="High Fit",
        recommendation="go"))

    async def fake_gen(account, score):
        assert score.recommendation == "go"               # score context handed in
        return Dossier(pain_points=["denials"], cost_usd=0.7,
                       generated_at="2026-06-04T00:00:00+00:00"), 0.7
    monkeypatch.setattr(svc_mod.dossier, "generate", fake_gen)

    out = await svc.generate_dossier("acc_x")
    assert out["dossier_state"] == "ready"
    assert out["dossier"]["pain_points"] == ["denials"]
    assert out["dossier_cost"] == 0.7
    assert repo.cost_summary()["total_cost"] >= 0.7       # dossier counts in the meter

    # only scored accounts qualify
    repo.upsert_account(Account(account_id="acc_q", name="Q", segment="payer",
                                framework="payer", source="csv"), state="queued")
    assert await svc.generate_dossier("acc_q") is None


# ── operational guards (budget, prod, corruption) ─────────────────────


class TestBudgetGuard:
    def test_assert_and_count(self):
        from auto_search.scoring import budget

        s = {"month_cost": 190, "monthly_budget": 200, "budget_remaining": 10}
        budget.assert_affordable(s, 5)                     # within budget: ok
        with pytest.raises(budget.BudgetExceeded):
            budget.assert_affordable(s, 20)                # would exceed
        assert budget.affordable_count(s, 0.35) == int(10 // 0.35)
        assert budget.affordable_count({"budget_remaining": 0}, 0.35) == 0
        assert budget.affordable_count(s, 0) >= 1          # free op: unbounded


def test_is_production_detection(monkeypatch):
    from auto_search import runtime

    for m in ("APP_ENV", "RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME",
              "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID"):
        monkeypatch.delenv(m, raising=False)
    assert runtime.is_production() is False                # bare localhost
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "p-123")
    assert runtime.is_production() is True                 # on Railway
    monkeypatch.setenv("APP_ENV", "dev")                   # explicit dev wins
    assert runtime.is_production() is False


def test_scoring_json_corrupt_is_backed_up_not_wiped(tmp_path):
    from auto_search.db.scoring_repository import ScoringJsonRepository

    p = tmp_path / "s.json"
    p.write_text("{ this is not valid json")
    repo = ScoringJsonRepository(path=str(p))              # _load on init
    assert repo.list_accounts() == []                     # starts empty
    assert (tmp_path / "s.json.corrupt").exists()         # but the bad file is preserved


def test_production_requires_database_url(monkeypatch):
    from auto_search.db.scoring_repository import get_scoring_repository

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError, match="DATABASE_URL is required"):
        get_scoring_repository()


# ── cost math (the money the meter reports) ───────────────────────────


class TestCallCost:
    def _resp(self, **usage):
        from types import SimpleNamespace
        return SimpleNamespace(usage=SimpleNamespace(**usage))

    def test_plain_input_output_plus_searches(self):
        from auto_search import llm
        # 10k in @ $3/M + 2k out @ $15/M = $0.06, + 3 searches @ $0.01 = $0.09
        resp = self._resp(input_tokens=10_000, output_tokens=2_000,
                          cache_creation_input_tokens=0, cache_read_input_tokens=0)
        assert llm.call_cost(resp, searches=3) == 0.09

    def test_cache_read_is_cheaper_than_fresh_input(self):
        from auto_search import llm
        # 9k cached-read @ $0.30/M is far cheaper than @ $3/M fresh input.
        # (1k in + 0.5k out + 9k cache_read)/1e6 + 1 search
        resp = self._resp(input_tokens=1_000, output_tokens=500,
                          cache_creation_input_tokens=0, cache_read_input_tokens=9_000)
        assert llm.call_cost(resp, searches=1) == 0.0232

    def test_missing_usage_is_zero(self):
        from types import SimpleNamespace

        from auto_search import llm
        assert llm.call_cost(SimpleNamespace(), searches=2) == 0.0
