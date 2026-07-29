"""Hourly SFDC pull riding the 15-min TOFU cron (MAR2-50 C).

Mount Sinai's BOFU form lead (Mary Taylor) waited ~24h for the daily cron.
run_linkedin_tofu.py provably ticks every 15 min, so every 4th ACTIVE-HOURS
tick (minute-of-hour 0-14 — derived, never a persisted counter) now runs the
same windowed idempotent SFDC pull run_daily uses, inline (import-and-call,
no subprocess), after the TOFU work — even when the TOFU leg is throttled.
The pull is API-free (SOQL only), bounded by a timeout so the tick can never
stretch past ~5 min, and isolated: a pull failure never fails the tick.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "run_linkedin_tofu_sfdc_leg",
    Path(__file__).resolve().parent.parent / "scripts" / "run_linkedin_tofu.py")
rlt = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rlt)


# ── minute-window gating ────────────────────────────────────────────────


def test_sfdc_leg_due_only_on_the_first_tick_of_the_hour():
    for minute in (0, 7, 14):
        assert rlt._sfdc_leg_due(datetime(2026, 7, 28, 15, minute, tzinfo=UTC))
    for minute in (15, 16, 30, 44, 45, 59):
        assert not rlt._sfdc_leg_due(datetime(2026, 7, 28, 15, minute, tzinfo=UTC))


# ── the leg runs even when the TOFU work is throttled ───────────────────


class _Repo:
    def __init__(self, last):
        self._last = last

    def ensure_schema(self):
        pass

    def set_setting(self, key, value):
        pass

    def get_sync_state(self, source=None):
        return {"last_synced_at": self._last} if self._last else None


def _recent() -> str:
    return (datetime.now(UTC) - timedelta(hours=1)).isoformat()


def _throttled_main(monkeypatch, *, due: bool) -> list:
    monkeypatch.setattr(rlt, "get_engagement_repository", lambda: _Repo(_recent()))
    monkeypatch.setenv("LINKEDIN_TOFU_CRON_ENABLED", "1")
    monkeypatch.setenv("LINKEDIN_TOFU_MIN_INTERVAL_HOURS", "6")
    monkeypatch.delenv("LINKEDIN_TOFU_ACTIVE_HOURS_UTC", raising=False)
    monkeypatch.delenv("LINKEDIN_TOFU_WEEKDAYS_ONLY", raising=False)
    monkeypatch.setattr(sys, "argv", ["run_linkedin_tofu.py"])
    monkeypatch.setattr(rlt, "_sfdc_leg_due", lambda now=None: due)
    ran: list = []
    monkeypatch.setattr(rlt, "_run_sfdc_leg", lambda repo: ran.append(repo))

    def _boom(**_kw):
        raise AssertionError("runner (Apify spend) must not run when throttled")
    monkeypatch.setattr(rlt.linkedin_ads_runner, "run", _boom)
    assert rlt.main() == 0
    return ran


def test_throttled_tick_still_runs_the_sfdc_leg_when_due(monkeypatch):
    assert len(_throttled_main(monkeypatch, due=True)) == 1


def test_throttled_tick_skips_the_sfdc_leg_when_not_due(monkeypatch):
    assert _throttled_main(monkeypatch, due=False) == []


def test_dry_run_never_runs_the_sfdc_leg(monkeypatch):
    monkeypatch.setattr(rlt, "get_engagement_repository", lambda: _Repo(_recent()))
    monkeypatch.setattr(sys, "argv", ["run_linkedin_tofu.py", "--dry-run"])
    monkeypatch.setattr(rlt, "_sfdc_leg_due", lambda now=None: True)
    ran: list = []
    monkeypatch.setattr(rlt, "_run_sfdc_leg", lambda repo: ran.append(repo))

    async def _quiet(**_kw):
        return {"dry_run": True, "stats": {}}
    monkeypatch.setattr(rlt.linkedin_ads_runner, "run", _quiet)
    assert rlt.main() == 0
    assert ran == []


# ── failure isolation + bounded runtime ─────────────────────────────────


def _leg_fakes(monkeypatch):
    monkeypatch.setattr(rlt, "get_scoring_repository", lambda: object())
    monkeypatch.setattr(rlt, "get_repository", lambda: object())


def test_sfdc_pull_failure_never_raises_out_of_the_leg(monkeypatch):
    _leg_fakes(monkeypatch)

    def _boom(**_kw):
        raise RuntimeError("salesforce down")
    monkeypatch.setattr("auto_search.engagement.sync.run_sfdc_sync", _boom)
    rlt._run_sfdc_leg(_Repo(None))          # must not raise


def test_sfdc_leg_pushes_notify_only_on_new_events(monkeypatch):
    _leg_fakes(monkeypatch)
    pushed: list = []
    monkeypatch.setattr(rlt.subprocess, "run",
                        lambda *a, **k: pushed.append(a) or type(
                            "R", (), {"returncode": 0})())
    monkeypatch.setattr("auto_search.engagement.sync.run_sfdc_sync",
                        lambda **_kw: {"new_events": 2})
    rlt._run_sfdc_leg(_Repo(None))
    assert len(pushed) == 1

    pushed.clear()
    monkeypatch.setattr("auto_search.engagement.sync.run_sfdc_sync",
                        lambda **_kw: {"new_events": 0})
    rlt._run_sfdc_leg(_Repo(None))
    assert pushed == []


def test_sfdc_leg_times_out_instead_of_hanging_the_tick(monkeypatch):
    _leg_fakes(monkeypatch)
    monkeypatch.setattr(rlt, "_SFDC_LEG_TIMEOUT_S", 0.05)
    import threading
    release = threading.Event()

    def _hang(**_kw):
        release.wait(5)
        return {"new_events": 0}
    monkeypatch.setattr("auto_search.engagement.sync.run_sfdc_sync", _hang)
    start = datetime.now(UTC)
    rlt._run_sfdc_leg(_Repo(None))          # returns promptly, no raise
    assert (datetime.now(UTC) - start).total_seconds() < 3
    release.set()                           # unblock the worker thread
