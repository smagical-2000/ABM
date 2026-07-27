"""Single-flight busy flags on the side-effectful endpoints.

COO QA 2026-07-27 found the check and the claim were not adjacent:
POST /api/campaigns/run tested `campaigns_running` BEFORE `await
_json_body(request)` and only set it several statements later, so the event
loop could hand a second request through the same open door — two live
enrollment passes over the same eligible accounts (the ledger double-records;
HeyReach add_leads has no 409-on-existing softener).
"""

from __future__ import annotations

import asyncio
import importlib

import pytest
from fastapi.testclient import TestClient

_app = importlib.import_module("auto_search.api.app")


@pytest.fixture
def client(tmp_path, monkeypatch):
    from auto_search.db.campaign_repository import CampaignJsonRepository
    from auto_search.db.engagement_repository import EngagementJsonRepository
    from auto_search.db.repository import JsonFileRepository
    from auto_search.db.scoring_repository import ScoringJsonRepository

    for var in ("BASIC_AUTH_USER", "BASIC_AUTH_PASS", "DATABASE_URL"):
        monkeypatch.delenv(var, raising=False)
    eng = EngagementJsonRepository(tmp_path / "eng.json")
    eng.set_setting("campaigns_live", "1")           # live mode ON
    monkeypatch.setattr(_app, "get_repository",
                        lambda: JsonFileRepository(tmp_path / "s.json"))
    monkeypatch.setattr(_app, "get_scoring_repository",
                        lambda: ScoringJsonRepository(tmp_path / "sc.json"))
    monkeypatch.setattr(_app, "get_engagement_repository", lambda: eng)
    monkeypatch.setattr(_app, "get_campaign_repository",
                        lambda: CampaignJsonRepository(tmp_path / "camp.json"))
    with TestClient(_app.create_app()) as c:
        yield c


def _endpoint(app, path: str):
    """The raw handler, so two requests can be interleaved deterministically
    (TestClient serializes; the race lives between two awaits inside one
    handler)."""
    return next(r for r in app.routes if getattr(r, "path", None) == path).endpoint


async def test_two_concurrent_live_runs_start_only_once(client, monkeypatch):
    app = client.app
    started: list[str] = []

    async def _fake_run(**kwargs):
        started.append(kwargs.get("trigger") or "?")
        return {"enrolled": 0, "accounts": []}

    monkeypatch.setattr(_app.campaigns_runner, "run", _fake_run)
    import auto_search.engagement.replyio_client as _rc
    monkeypatch.setattr(_rc, "ReplyioClient", lambda *a, **k: object())

    # Both requests park inside the handler exactly where the real one awaits
    # the request body — the window the busy check used to leave open.
    gate = asyncio.Event()

    async def _slow_body(_request):
        await gate.wait()
        return {"dry_run": False}

    monkeypatch.setattr(_app, "_json_body", _slow_body)

    run = _endpoint(app, "/api/campaigns/run")
    first = asyncio.create_task(run(object()))
    second = asyncio.create_task(run(object()))
    await asyncio.sleep(0)                    # let both enter and park
    gate.set()
    a, b = await asyncio.gather(first, second)

    assert sorted([bool(a.get("started")), bool(b.get("started"))]) == [False, True]
    assert (a.get("busy") or b.get("busy")) is True
    await asyncio.sleep(0)                    # let the scheduled pass run
    assert len(started) == 1                  # exactly ONE enrollment pass


async def test_live_run_still_starts_when_idle(client, monkeypatch):
    """The claim must not deadlock the normal single-caller path."""
    app = client.app
    monkeypatch.setattr(_app.campaigns_runner, "run",
                        lambda **_kw: _done({"enrolled": 0, "accounts": []}))
    import auto_search.engagement.replyio_client as _rc
    monkeypatch.setattr(_rc, "ReplyioClient", lambda *a, **k: object())
    monkeypatch.setattr(_app, "_json_body",
                        lambda _r: _done({"dry_run": False}))

    run = _endpoint(app, "/api/campaigns/run")
    out = await run(object())
    assert out == {"started": True, "dry_run": False}
    await asyncio.sleep(0)
    assert app.state.campaigns_running is False   # released when the pass ends


async def test_dry_run_releases_the_flag(client, monkeypatch):
    """A dry pass returns inline; it must never leave the endpoint wedged."""
    app = client.app
    monkeypatch.setattr(_app.campaigns_runner, "run",
                        lambda **_kw: _done({"enrolled": 0, "accounts": []}))
    monkeypatch.setattr(_app, "_json_body", lambda _r: _done({"dry_run": True}))

    run = _endpoint(app, "/api/campaigns/run")
    out = await run(object())
    assert out["dry_run"] is True
    assert app.state.campaigns_running is False


async def _done(value):
    return value
