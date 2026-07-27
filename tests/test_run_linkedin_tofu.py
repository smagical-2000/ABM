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


def test_stamp_persists_last_synced_at_for_throttle(tmp_path):
    """Regression: the run stamp must actually set last_synced_at (status='ok' does NOT
    auto-stamp — the runner passes it explicitly), else the throttle never reads a time."""
    from auto_search.db.engagement_repository import EngagementJsonRepository
    repo = EngagementJsonRepository(path=str(tmp_path / "e.json"))
    repo.set_sync_state(source=rlt._SYNC_SOURCE, status="success",
                        stats={"scanned": 1}, last_synced_at=datetime.now(UTC))
    st = repo.get_sync_state(source=rlt._SYNC_SOURCE)
    assert st is not None
    hrs = rlt._hours_since(st.get("last_synced_at"))
    assert hrs is not None and hrs < 0.1        # stamped ~now → throttle would fire


def _armed_for_a_live_run(monkeypatch, repo):
    """Env + stubs so main() reaches the runner (throttle disarmed, no writes)."""
    monkeypatch.setattr(rlt, "get_engagement_repository", lambda: repo)
    monkeypatch.setenv("LINKEDIN_TOFU_CRON_ENABLED", "1")
    monkeypatch.setenv("LINKEDIN_TOFU_MIN_INTERVAL_HOURS", "6")
    monkeypatch.delenv("LINKEDIN_TOFU_ACTIVE_HOURS_UTC", raising=False)
    monkeypatch.delenv("LINKEDIN_TOFU_WEEKDAYS_ONLY", raising=False)
    monkeypatch.setattr(sys, "argv", ["run_linkedin_tofu.py"])
    monkeypatch.setattr(rlt, "get_scoring_repository", lambda: object())
    monkeypatch.setattr(rlt, "get_repository", lambda: object())
    import auto_search.engagement.airtable_client as ac
    import auto_search.engagement.replyio_client as rc
    monkeypatch.setattr(ac, "AirtableClient", lambda *a, **k: object())
    monkeypatch.setattr(rc, "ReplyioClient", lambda *a, **k: object())


def test_failed_run_stamps_sync_state_so_the_throttle_still_applies(monkeypatch, tmp_path):
    """A crash AFTER the paid Apify scrape (Airtable client raising, a DB flap
    inside cross_and_persist) used to leave sync_state untouched: the next tick
    read a stale last_synced_at and re-ran the FULL paid scan, up to 4x/hour for
    the rest of the active window, with at most one Slack alert per 3 hours."""
    from auto_search.db.engagement_repository import EngagementJsonRepository
    repo = EngagementJsonRepository(path=str(tmp_path / "e.json"))
    _armed_for_a_live_run(monkeypatch, repo)

    async def _boom(**_kw):
        raise RuntimeError("cross_and_persist: connection reset")
    monkeypatch.setattr(rlt.linkedin_ads_runner, "run", _boom)

    assert rlt.main() == 1                       # the run still reports failure
    state = repo.get_sync_state(source=rlt._SYNC_SOURCE)
    assert state is not None                     # was None → throttle disarmed
    assert state["status"] == "failed"           # and it is NOT recorded as a success
    hrs = rlt._hours_since(state.get("last_synced_at"))
    assert hrs is not None and hrs < 0.1

    # The very next tick (15 min later) must now be throttled — no re-scrape.
    def _must_not_run(**_kw):
        raise AssertionError("a failed run must not re-bill Apify on the next tick")
    monkeypatch.setattr(rlt.linkedin_ads_runner, "run", _must_not_run)
    assert rlt.main() == 0


def test_dry_run_failure_does_not_stamp(monkeypatch, tmp_path):
    """--dry-run never spends, so it must not arm the cost throttle either."""
    from auto_search.db.engagement_repository import EngagementJsonRepository
    repo = EngagementJsonRepository(path=str(tmp_path / "e.json"))
    _armed_for_a_live_run(monkeypatch, repo)
    monkeypatch.setattr(sys, "argv", ["run_linkedin_tofu.py", "--dry-run"])

    async def _boom(**_kw):
        raise RuntimeError("nope")
    monkeypatch.setattr(rlt.linkedin_ads_runner, "run", _boom)

    assert rlt.main() == 1
    assert repo.get_sync_state(source=rlt._SYNC_SOURCE) is None


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
