"""Integration tests: the RunControl gate actually governs pipeline spend.

The unit tests in test_run_control.py pin the gate's own behaviour. These prove
the *contract with the pipeline*: a cancel stops before the next (paid) qualify,
and a pause freezes qualification until resumed. We count `qualify` calls as the
proxy for spend, because one qualify == one paid Claude call.
"""

import asyncio
from datetime import UTC, datetime

import pytest

from auto_search import pipeline
from auto_search.models import QualificationResult, RawSignal
from auto_search.run_control import RunControl


class _FakeConnector:
    """Yields one leadership signal per company name (each a unique company)."""

    def __init__(self, names):
        self._names = names

    async def pull(self, *, since):
        for i, name in enumerate(self._names):
            yield RawSignal(
                source="signalbase_leadership", source_external_id=f"{name}::{i}",
                signal_type="leadership_change", company_name_raw=name,
                observed_at=datetime(2026, 6, 5, tzinfo=UTC), signal_strength=0.9)


def _patch_qualify(monkeypatch):
    """Replace the (paid) qualifier with a counter; returns the call log."""
    calls = []

    async def fake_qualify(signal):
        calls.append(signal.company_name_raw)
        return QualificationResult(qualified=True, confidence=0.9,
                                   reasoning="fit", segment="health_system")

    monkeypatch.setattr(pipeline, "qualify", fake_qualify)
    return calls


SINCE = datetime(2026, 6, 1, tzinfo=UTC)


@pytest.mark.asyncio
async def test_cancel_stops_before_qualifying_remaining(monkeypatch):
    calls = _patch_qualify(monkeypatch)
    connector = _FakeConnector(["Alpha Health", "Beta Health", "Gamma Health"])

    ctrl = RunControl()
    # Allow exactly one company, then cancel so the rest are never qualified.
    real_gate = ctrl.gate

    async def gate_then_cancel():
        ok = await real_gate()
        ctrl.cancel()           # after the 1st pass, request stop
        return ok

    out = [c async for c in pipeline.run(connector, SINCE, gate=gate_then_cancel)]

    assert len(calls) == 1          # only one paid qualify happened
    assert len(out) == 1            # only one candidate yielded
    assert out[0].company_name == "Alpha Health"


@pytest.mark.asyncio
async def test_pause_freezes_qualification_until_resumed(monkeypatch):
    calls = _patch_qualify(monkeypatch)
    connector = _FakeConnector(["Alpha Health", "Beta Health"])

    ctrl = RunControl()
    ctrl.pause()                    # paused before the run consumes anything

    async def consume():
        return [c async for c in pipeline.run(connector, SINCE, gate=ctrl.gate)]

    task = asyncio.ensure_future(consume())
    await asyncio.sleep(0.15)
    assert calls == []              # spend frozen: nothing qualified while paused
    assert not task.done()

    ctrl.resume()
    out = await asyncio.wait_for(task, timeout=2.0)
    assert len(calls) == 2          # both companies qualified after resume
    assert len(out) == 2


@pytest.mark.asyncio
async def test_no_gate_qualifies_everything(monkeypatch):
    """Sanity: without a gate the pipeline behaves exactly as before."""
    calls = _patch_qualify(monkeypatch)
    connector = _FakeConnector(["Alpha Health", "Beta Health"])
    out = [c async for c in pipeline.run(connector, SINCE)]
    assert len(calls) == 2 and len(out) == 2
