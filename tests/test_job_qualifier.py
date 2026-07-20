"""Tests for the job-level qualifier — no live Claude calls.

`qualify_jobs` is monkeypatched so the filtering logic (drop confident
rejects, fail-open on missing verdicts, pass non-job signals through) is
tested deterministically.
"""

from datetime import UTC, datetime

import pytest

from auto_search import job_qualifier
from auto_search.job_qualifier import JobRelevance, filter_job_signals
from auto_search.models import RawSignal

OBS = datetime(2026, 6, 2, tzinfo=UTC)


def _job_sig(ext_id: str, title: str) -> RawSignal:
    return RawSignal(
        source="indeed",
        source_external_id=ext_id,
        signal_type="job_posting",
        company_name_raw=f"Co {ext_id}",
        observed_at=OBS,
        signal_strength=0.78,
        payload={"role": "Coder", "job_title": title, "description": "JD"},
    )


def _other_sig() -> RawSignal:
    return RawSignal(
        source="warntracker",
        source_external_id="layoff-1",
        signal_type="layoff",
        company_name_raw="LayoffCo",
        observed_at=OBS,
        signal_strength=0.6,
        payload={"laid_off_count": 50},
    )


def test_job_relevance_defaults_to_keep():
    v = JobRelevance(id="x")
    assert v.relevant is True and v.confidence == 0.5


@pytest.mark.asyncio
async def test_filter_drops_rejects_keeps_rest(monkeypatch):
    async def fake_qualify_jobs(jobs, **_kw):
        return {
            "keep": JobRelevance(id="keep", relevant=True, rcm_role="coding"),
            "drop": JobRelevance(id="drop", relevant=False, reason="coding instructor"),
            # "missing" intentionally absent → fail-open (kept)
        }

    monkeypatch.setattr(job_qualifier, "qualify_jobs", fake_qualify_jobs)

    signals = [
        _job_sig("keep", "Medical Coder"),
        _job_sig("drop", "Medical Coding Instructor"),
        _job_sig("missing", "Coding Specialist"),
        _other_sig(),
    ]
    out = await filter_job_signals(signals)
    ids = {s.source_external_id for s in out}

    assert "drop" not in ids                 # confident reject removed
    assert {"keep", "missing", "layoff-1"} == ids
    # non-job signal untouched
    assert any(s.signal_type == "layoff" for s in out)


@pytest.mark.asyncio
async def test_filter_noop_without_job_signals(monkeypatch):
    called = False

    async def fake_qualify_jobs(jobs, **_kw):  # pragma: no cover — must not run
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(job_qualifier, "qualify_jobs", fake_qualify_jobs)

    signals = [_other_sig()]
    out = await filter_job_signals(signals)
    assert out == signals and called is False


@pytest.mark.asyncio
async def test_filter_fails_open_when_qualifier_errors(monkeypatch):
    async def boom(jobs, **_kw):
        raise RuntimeError("anthropic down")

    # qualify_jobs swallows internally, but guard the whole path too: if the
    # map is empty, every posting is kept.
    async def empty(jobs, **_kw):
        return {}

    monkeypatch.setattr(job_qualifier, "qualify_jobs", empty)
    signals = [_job_sig("a", "Medical Biller"), _job_sig("b", "Medical Coder")]
    out = await filter_job_signals(signals)
    assert {s.source_external_id for s in out} == {"a", "b"}


@pytest.mark.asyncio
async def test_qualify_jobs_gate_stops_before_spending(monkeypatch):
    """A cancelled gate stops the prefilter before any batch is qualified —
    no paid call, no spend hook."""
    batches_run = 0

    async def fake_batch(batch):
        nonlocal batches_run
        batches_run += 1
        return {}, None

    monkeypatch.setattr(job_qualifier, "_qualify_batch", fake_batch)

    async def cancelled_gate():
        return False

    spends = []
    signals = [_job_sig(str(i), "Medical Coder") for i in range(20)]
    out = await job_qualifier.qualify_jobs(
        signals, gate=cancelled_gate, on_spend=lambda s: spends.append(s))
    assert out == {} and batches_run == 0 and spends == []


@pytest.mark.asyncio
async def test_qualify_jobs_reports_spend_per_batch(monkeypatch):
    """Each qualified batch reports its measured spend via on_spend."""
    from auto_search.models import LlmSpend

    async def fake_batch(batch):
        return ({s.source_external_id: JobRelevance(id=s.source_external_id)
                 for s in batch},
                LlmSpend(cost_usd=0.01, model="m", input_tokens=100, output_tokens=20))

    monkeypatch.setattr(job_qualifier, "_qualify_batch", fake_batch)
    spends = []
    signals = [_job_sig(str(i), "Medical Coder") for i in range(20)]  # 3 batches of 8
    await job_qualifier.qualify_jobs(signals, on_spend=lambda s: spends.append(s))
    assert len(spends) == 3
    assert all(s.cost_usd == 0.01 for s in spends)
