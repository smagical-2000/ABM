"""Cost-guard throttle on the LinkedIn TOFU cron.

The Railway cron ticks every 15 min, but re-scraping/re-enriching every tick was ~$12/day
of Apify. A tick within LINKEDIN_TOFU_MIN_INTERVAL_HOURS must skip BEFORE any spend (the
runner is never called); a tick past the interval runs normally. Loaded via importlib
since the entry point lives in scripts/ (not a package)."""

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "run_linkedin_tofu",
    Path(__file__).resolve().parent.parent / "scripts" / "run_linkedin_tofu.py")
rlt = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rlt)


def _iso(hours_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()


class _Repo:
    def __init__(self, last):
        self._last = last
    def ensure_schema(self):
        pass
    def get_sync_state(self, source=None):
        return {"last_synced_at": self._last} if self._last else None


def test_hours_since_parses_and_tolerates_junk():
    assert rlt._hours_since(None) is None
    assert rlt._hours_since("not-a-date") is None
    assert 0.9 < rlt._hours_since(_iso(1)) < 1.1
    assert rlt._hours_since(_iso(6)) > 5.9


def test_throttle_skips_recent_run_before_any_spend(monkeypatch):
    """Last real run 1h ago (< 6h) → main() returns 0 and NEVER calls the runner."""
    monkeypatch.setattr(rlt, "get_engagement_repository", lambda: _Repo(_iso(1)))
    monkeypatch.setenv("LINKEDIN_TOFU_CRON_ENABLED", "1")
    monkeypatch.setenv("LINKEDIN_TOFU_MIN_INTERVAL_HOURS", "6")
    monkeypatch.setattr(sys, "argv", ["run_linkedin_tofu.py"])

    def _boom(**_kw):
        raise AssertionError("runner (Apify spend) must not run when throttled")
    monkeypatch.setattr(rlt.linkedin_ads_runner, "run", _boom)

    assert rlt.main() == 0


def test_no_throttle_when_no_prior_run(monkeypatch):
    """First run ever (no sync_state) is not throttled — it must proceed to the runner."""
    monkeypatch.setattr(rlt, "get_engagement_repository", lambda: _Repo(None))
    monkeypatch.setenv("LINKEDIN_TOFU_CRON_ENABLED", "1")
    monkeypatch.setenv("LINKEDIN_TOFU_MIN_INTERVAL_HOURS", "6")
    monkeypatch.setattr(sys, "argv", ["run_linkedin_tofu.py", "--max-contacts", "0"])

    called = {}

    async def _fake_run(**_kw):
        called["ran"] = True
        return {"stats": {"scanned": 0}}
    monkeypatch.setattr(rlt.linkedin_ads_runner, "run", _fake_run)
    # a first run has no prior timestamp → not throttled → runner is reached
    monkeypatch.setattr(_Repo, "get_sync_state", lambda self, source=None: None)
    # stub the write clients + repos the runner args need (never used by the fake run)
    monkeypatch.setattr(rlt, "get_scoring_repository", lambda: object())
    monkeypatch.setattr(rlt, "get_repository", lambda: object())
    import auto_search.engagement.airtable_client as ac
    import auto_search.engagement.replyio_client as rc
    monkeypatch.setattr(ac, "AirtableClient", lambda *a, **k: object())
    monkeypatch.setattr(rc, "ReplyioClient", lambda *a, **k: object())
    # stamping the run uses set_sync_state — make it a no-op
    monkeypatch.setattr(_Repo, "set_sync_state", lambda self, **k: None, raising=False)

    assert rlt.main() == 0
    assert called.get("ran") is True
