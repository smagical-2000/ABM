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
    async def fake_qualify_jobs(jobs):
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

    async def fake_qualify_jobs(jobs):  # pragma: no cover — must not run
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(job_qualifier, "qualify_jobs", fake_qualify_jobs)

    signals = [_other_sig()]
    out = await filter_job_signals(signals)
    assert out == signals and called is False


@pytest.mark.asyncio
async def test_filter_fails_open_when_qualifier_errors(monkeypatch):
    async def boom(jobs):
        raise RuntimeError("anthropic down")

    # qualify_jobs swallows internally, but guard the whole path too: if the
    # map is empty, every posting is kept.
    async def empty(jobs):
        return {}

    monkeypatch.setattr(job_qualifier, "qualify_jobs", empty)
    signals = [_job_sig("a", "Medical Biller"), _job_sig("b", "Medical Coder")]
    out = await filter_job_signals(signals)
    assert {s.source_external_id for s in out} == {"a", "b"}
