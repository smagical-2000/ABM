"""Spend failsafe tests — per-account spike, per-operation overheat, cost events.

No live LLM: the engine + QA are monkeypatched so the guard logic is exercised
deterministically.
"""

from datetime import UTC, datetime

import pytest

from auto_search.db.scoring_repository import ScoringJsonRepository
from auto_search.scoring import spend_guard
from auto_search.scoring.models import Account, Dimension, QAResult, ScoreResult


def _repo(tmp_path):
    return ScoringJsonRepository(path=str(tmp_path / "s.json"))


def test_record_company_qualify_rules_are_free(tmp_path):
    from auto_search.models import CompanyCandidate, QualificationResult, RawSignal

    repo = _repo(tmp_path)
    op = spend_guard.Operation(repo, "discovery_manual", estimated_usd=1.0)
    cand = CompanyCandidate(
        company_key="acme",
        company_name="Acme",
        signals=[RawSignal(
            source="warntracker", source_external_id="acme::1",
            signal_type="layoff", company_name_raw="Acme",
            observed_at=datetime(2025, 1, 1, tzinfo=UTC), payload={})],
        qualification=QualificationResult(
            qualified=False, confidence=0.95,
            reasoning="Pre-filter", decided_by="rules"),
    )
    spend_guard.record_company_qualify(op, cand)
    assert op.actual == 0.0
    assert repo._events[-1]["actual_usd"] == 0.0
    assert repo._events[-1].get("metadata") is None


def test_record_company_qualify_measured_tokens(tmp_path, monkeypatch):
    from auto_search.models import CompanyCandidate, LlmSpend, QualificationResult, RawSignal

    monkeypatch.setenv("DISCOVERY_EST_QUAL_COST", "0.12")
    repo = _repo(tmp_path)
    op = spend_guard.Operation(repo, "discovery_manual", estimated_usd=1.0)
    cand = CompanyCandidate(
        company_key="cardiology",
        company_name="Cardiology Group",
        signals=[RawSignal(
            source="signalbase_jobs", source_external_id="card::1",
            signal_type="job", company_name_raw="Cardiology Group",
            observed_at=datetime(2025, 1, 1, tzinfo=UTC), payload={})],
        qualification=QualificationResult(
            qualified=False, confidence=0.8, reasoning="review",
            decided_by="rules+llm",
            llm_spend=LlmSpend(
                cost_usd=0.0842, model="claude-sonnet-4-5",
                searches=2, input_tokens=1200, output_tokens=400),
        ),
    )
    spend_guard.record_company_qualify(op, cand)
    assert op.actual == 0.0842
    ev = repo._events[-1]
    assert ev["company_key"] == "cardiology"
    assert ev["metadata"]["measured"] is True
    assert ev["estimated_usd"] == 0.12


def test_operation_account_cap_and_cost_events(tmp_path):
    """A single account past $10 trips the per-account cap; every step is a
    persisted cost_event; finish records the operation."""
    repo = _repo(tmp_path)
    op = spend_guard.Operation(repo, "score_batch", estimated_usd=1.0, accounts_planned=10)

    op.record(step="score", actual_usd=4.0, account_id="a")
    assert not op.account_over_cap("a")

    op.record(step="qa", actual_usd=7.0, account_id="a")   # a total 11 > $10
    assert op.account_over_cap("a")
    assert op.account_cost("a") == 11.0

    assert len(repo._events) == 2                           # cost_events persisted
    op.finish()
    assert repo._ops[0]["status"] == "overheated"           # 11 > est 1.0 * 1.4
    assert repo._ops[0]["actual_usd"] == 11.0


def test_overheat_at_140_percent(tmp_path):
    repo = _repo(tmp_path)
    op = spend_guard.Operation(repo, "score_batch", estimated_usd=10.0)
    op.record(step="score", actual_usd=13.0)               # 1.3x — under
    assert not op.overheated()
    op.record(step="score", actual_usd=1.5)                # total 14.5 = 1.45x
    assert op.overheated()


def test_needs_confirmation(monkeypatch):
    monkeypatch.setenv("SPEND_MAX_OP_ESTIMATE_USD", "150")
    assert not spend_guard.needs_confirmation(149.0)
    assert spend_guard.needs_confirmation(151.0)
    assert spend_guard.estimate_batch(100, 0.3) == 30.0


def _seed_account(repo, account_id, source="discovery"):
    repo.upsert_account(Account(account_id=account_id, name=account_id.upper(),
                                segment="payer", framework="payer", source=source),
                        state="queued")


@pytest.mark.asyncio
async def test_service_per_account_overheat_skips_qa(monkeypatch, tmp_path):
    """If one account's scorer cost blows past the per-account cap, it drops to
    'error' and QA is skipped — no more LLM spent on it."""
    from auto_search.scoring import service as svc_mod
    from auto_search.scoring.service import ScoringService

    repo = _repo(tmp_path)
    svc = ScoringService(repo)
    _seed_account(repo, "acc_x")

    async def runaway(account, prior=None):
        return ScoreResult(account_id=account.account_id, framework="payer",
                           framework_version="v",
                           dimensions=[Dimension(key="k", label="K", score=5, max=10)],
                           total=5, max_total=10, tier_band="high", tier_label="High Fit",
                           cost_usd=12.0)                    # $12 > $10 cap
    qa_called = {"v": False}

    async def fake_qa(*a, **k):
        qa_called["v"] = True
        return QAResult(status="verified"), 0.1
    monkeypatch.setattr(svc_mod.engine, "score_account", runaway)
    monkeypatch.setattr(svc_mod.qa, "qa_account", fake_qa)

    op = spend_guard.Operation(repo, "score_batch", estimated_usd=5.0, accounts_planned=1)
    out = await svc.run_scoring("acc_x", op=op)
    assert out["state"] == "error" and "overheat" in (out["error"] or "")
    assert qa_called["v"] is False                          # stopped before QA


@pytest.mark.asyncio
async def test_batch_continues_when_one_account_overheats(monkeypatch, tmp_path):
    """One runaway account does not stop the others — the batch keeps scoring."""
    from auto_search.scoring import service as svc_mod
    from auto_search.scoring.service import ScoringService

    repo = _repo(tmp_path)
    svc = ScoringService(repo)
    _seed_account(repo, "acc_a")
    _seed_account(repo, "acc_b")

    async def eng(account, prior=None):
        cost = 12.0 if account.account_id == "acc_a" else 0.2
        return ScoreResult(account_id=account.account_id, framework="payer",
                           framework_version="v",
                           dimensions=[Dimension(key="k", label="K", score=2, max=10)],
                           total=2, max_total=10, tier_band="low", tier_label="Low Fit",
                           cost_usd=cost)                    # low -> QA skipped, no mock needed
    monkeypatch.setattr(svc_mod.engine, "score_account", eng)

    op = spend_guard.Operation(repo, "score_batch", estimated_usd=5.0, accounts_planned=2)
    a = await svc.run_scoring("acc_a", op=op)
    b = await svc.run_scoring("acc_b", op=op)
    assert a["state"] == "error" and "overheat" in (a["error"] or "")
    assert b["state"] == "scored"                           # the other still completes
    # both scorer steps recorded as cost_events
    assert sum(1 for e in repo._events if e["step"] == "score") == 2
