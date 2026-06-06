"""Unit tests for RunControl — the pause/cancel gate used to cap discovery spend.

These exercise the gate in isolation (no pipeline, no HTTP) so the contract is
pinned: paused blocks, resume unblocks, cancel stops, reset clears.
"""

import asyncio

import pytest

from auto_search.run_control import RunControl


@pytest.mark.asyncio
async def test_gate_passes_when_idle():
    ctrl = RunControl()
    assert await ctrl.gate() is True


@pytest.mark.asyncio
async def test_cancel_makes_gate_return_false():
    ctrl = RunControl()
    ctrl.cancel()
    assert ctrl.cancelled is True
    assert await ctrl.gate() is False


@pytest.mark.asyncio
async def test_pause_blocks_until_resumed():
    """A paused gate must not return until something flips paused off."""
    ctrl = RunControl()
    ctrl.pause()
    gate = asyncio.ensure_future(ctrl.gate())

    # Give the gate a moment; while paused it must stay pending.
    await asyncio.sleep(0.1)
    assert not gate.done()

    ctrl.resume()
    assert await asyncio.wait_for(gate, timeout=1.0) is True


@pytest.mark.asyncio
async def test_cancel_unblocks_a_paused_gate():
    """Cancel clears paused too, so a paused run can observe the cancel."""
    ctrl = RunControl()
    ctrl.pause()
    gate = asyncio.ensure_future(ctrl.gate())
    await asyncio.sleep(0.1)
    assert not gate.done()

    ctrl.cancel()
    assert await asyncio.wait_for(gate, timeout=1.0) is False


@pytest.mark.asyncio
async def test_reset_clears_prior_state():
    ctrl = RunControl()
    ctrl.pause()
    ctrl.cancel()
    ctrl.reset()
    assert ctrl.paused is False and ctrl.cancelled is False
    assert await ctrl.gate() is True
