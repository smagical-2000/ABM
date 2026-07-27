"""The Reply.io resume cursor may only advance on a SUCCESSFUL sync.

`set_sync_state` stamps last_synced_at on both success AND failure, and
run_sync records status='failed' with a fresh stamp on every exception. The
windowed default resumed from that stamp minus a 10-day overlap without ever
checking status, so a failing daily run walked the cursor forward while
ingesting nothing: an outage longer than 10 days (revoked API key, Reply.io
5xxs past the retry cap) permanently loses the clicks/replies inside it, and
the affected accounts' heat/tier are silently depressed forever.

The anchor is now `window_to`, which only a successful sync ever writes.
Loaded via importlib since the entry point lives in scripts/ (not a package).
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "run_engagement_replyio",
    Path(__file__).resolve().parent.parent / "scripts" / "run_engagement_replyio.py")
rer = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rer)

NOW = datetime.now(UTC)


class _Repo:
    def __init__(self, state):
        self._state = state

    def get_sync_state(self, source="replyio"):
        return self._state


def _d(days_ago: int) -> str:
    return (NOW - timedelta(days=days_ago)).date().isoformat()


def test_never_synced_pulls_the_full_cohort():
    assert rer._default_since(_Repo(None)) == "2026-01-01"


def test_successful_sync_resumes_from_its_window_minus_overlap():
    repo = _Repo({"status": "success", "window_from": _d(11), "window_to": _d(1),
                  "last_synced_at": (NOW - timedelta(days=1)).isoformat()})
    assert rer._default_since(repo) == _d(11)


def test_failed_runs_do_not_walk_the_cursor_forward():
    """The 12-day-outage case: every failing daily run re-stamped last_synced_at,
    so the first run after recovery started INSIDE the outage and days 0-2 of it
    were never ingested. The cursor must still sit on the last SUCCESS."""
    repo = _Repo({"status": "failed", "error": "401 Unauthorized",
                  "window_from": _d(22), "window_to": _d(12),   # last SUCCESSFUL window
                  "last_synced_at": NOW.isoformat()})           # stamped by the failure
    since = rer._default_since(repo)
    assert since == _d(22)                       # 12d ago − 10d overlap
    assert since != _d(10)                       # NOT now − 10d (the old behaviour)


def test_repeated_failures_never_drift():
    state = {"status": "failed", "window_to": _d(30),
             "last_synced_at": NOW.isoformat()}
    assert rer._default_since(_Repo(state)) == _d(40)
    state["last_synced_at"] = (NOW + timedelta(hours=1)).isoformat()
    assert rer._default_since(_Repo(state)) == _d(40)


def test_failure_with_no_prior_success_gets_a_bounded_window():
    # Review 2026-07-27: a full-cohort re-pull on EVERY failed retry can itself
    # exceed Reply.io's rate caps and loop forever. A failed row with a stamp
    # anchors on it with a 30-day guard band; outages beyond that page via the
    # sync-staleness tripwire. Only a NEVER-synced store re-pulls the cohort.
    repo = _Repo({"status": "failed", "last_synced_at": NOW.isoformat()})
    expected = (NOW.date() - timedelta(days=30)).isoformat()
    assert rer._default_since(repo) == expected


def test_never_synced_store_repulls_the_cohort():
    repo = _Repo({})
    assert rer._default_since(repo) == "2026-01-01"


def test_legacy_success_row_without_window_to_uses_last_synced_at():
    # Rows written before window_from/window_to existed: a SUCCESS stamp is
    # still a trustworthy cursor.
    repo = _Repo({"status": "success",
                  "last_synced_at": (NOW - timedelta(days=2)).isoformat()})
    assert rer._default_since(repo) == _d(12)


def test_postgres_date_object_anchor_parses():
    # engagement_sync_state.window_to is a DATE column, so the Postgres repo
    # hands back a datetime.date, not a string.
    repo = _Repo({"status": "success", "window_to": (NOW - timedelta(days=1)).date()})
    assert rer._default_since(repo) == _d(11)


def test_unparseable_anchor_falls_back_to_the_cohort():
    assert rer._default_since(_Repo({"status": "success",
                                     "window_to": "not-a-date"})) == "2026-01-01"


def test_broken_repo_falls_back_to_the_cohort():
    class _Boom:
        def get_sync_state(self, source="replyio"):
            raise RuntimeError("db down")

    assert rer._default_since(_Boom()) == "2026-01-01"


def test_failed_legacy_row_gets_bounded_window_not_full_cohort(tmp_path):
    """Review 2026-07-27: a pre-upgrade row (no window_to) whose latest run
    FAILED must not trigger an unbounded cohort re-pull on every retry — it
    anchors on the stamp with a 30-day guard band instead."""
    from auto_search.db.engagement_repository import EngagementJsonRepository
    repo = EngagementJsonRepository(tmp_path / "e.json")
    repo.set_sync_state(source="replyio", status="failed",
                        last_synced_at="2026-07-20T12:00:00+00:00")
    got = rer._default_since(repo)
    assert got == "2026-06-20"          # 30-day band, NOT 2026-01-01
