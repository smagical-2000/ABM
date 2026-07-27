"""Sync staleness tripwire (2026-07-27 TOFU outage).

The exact failure being pinned: linkedin_tofu's sync froze at Jul 24 22:45
while the digest kept printing it as "(success)" — three days of missed TOFU
leads with zero alarms. These tests replay that shape and the weekend
mechanics of the weekday-cron sources.
"""

from __future__ import annotations

from datetime import UTC, datetime

from auto_search.ops import sync_staleness


class FakeRepo:
    def __init__(self, sync: dict[str, str]):
        self._sync = sync
        self._settings: dict[str, str] = {}

    def get_sync_state(self, source):
        at = self._sync.get(source)
        return {"last_synced_at": at, "status": "success"} if at else None

    def get_setting(self, key):
        return self._settings.get(key)

    def set_setting(self, key, value):
        self._settings[key] = value


# Monday Jul 27 2026 14:00 UTC — the actual outage-discovery moment.
MONDAY = datetime(2026, 7, 27, 14, 0, tzinfo=UTC)
WEDNESDAY = datetime(2026, 7, 29, 14, 0, tzinfo=UTC)
SATURDAY = datetime(2026, 7, 25, 14, 0, tzinfo=UTC)

FRESH = {
    "linkedin_tofu": "2026-07-27T13:45:00+00:00",
    "sfdc": "2026-07-27T12:31:00+00:00",
    "replyio": "2026-07-27T12:31:00+00:00",
    "podcast": "2026-07-27T12:31:00+00:00",
}


def _row(rows, source):
    return next(r for r in rows if r["source"] == source)


def test_the_jul27_outage_shape_breaches():
    """3-day-old tofu sync + 13-day-old replyio MUST breach — the digest's
    '(success)' label is exactly what hid the outage."""
    repo = FakeRepo({**FRESH,
                     "linkedin_tofu": "2026-07-24T22:45:00+00:00",
                     "replyio": "2026-07-14T18:35:00+00:00"})
    rows = sync_staleness.compute_staleness(repo, now=MONDAY)
    assert _row(rows, "linkedin_tofu")["breached"]
    assert _row(rows, "replyio")["breached"]
    assert not _row(rows, "sfdc")["breached"]


def test_all_fresh_no_breach():
    rows = sync_staleness.compute_staleness(FakeRepo(FRESH), now=MONDAY)
    assert not any(r["breached"] for r in rows)


def test_monday_allows_the_weekend_gap():
    """Friday's 12:30 run is ~73h old on Monday 14:00 — healthy, not stale."""
    repo = FakeRepo({**FRESH, "sfdc": "2026-07-24T12:31:00+00:00"})
    rows = sync_staleness.compute_staleness(repo, now=MONDAY)
    assert not _row(rows, "sfdc")["breached"]


def test_midweek_uses_the_tight_threshold():
    """The same ~73h gap on a Wednesday IS a breach."""
    repo = FakeRepo({**FRESH, "sfdc": "2026-07-26T12:31:00+00:00"})
    rows = sync_staleness.compute_staleness(repo, now=WEDNESDAY)
    assert _row(rows, "sfdc")["breached"]


def test_weekend_skips_weekday_sources_but_not_tofu():
    """Sat: daily-cron sources can't run (no breach even when old); the
    15-min tofu cron runs every day, so IT still breaches."""
    repo = FakeRepo({"linkedin_tofu": "2026-07-24T10:00:00+00:00",
                     "sfdc": "2026-07-24T12:31:00+00:00",
                     "replyio": "2026-07-24T12:31:00+00:00",
                     "podcast": "2026-07-24T12:31:00+00:00"})
    rows = sync_staleness.compute_staleness(repo, now=SATURDAY)
    assert _row(rows, "linkedin_tofu")["breached"]
    assert not _row(rows, "sfdc")["breached"]
    assert not _row(rows, "replyio")["breached"]


def test_never_synced_always_breaches():
    rows = sync_staleness.compute_staleness(
        FakeRepo({k: v for k, v in FRESH.items() if k != "podcast"}), now=MONDAY)
    assert _row(rows, "podcast")["breached"]
    assert _row(rows, "podcast")["hours"] is None


def test_breach_posts_one_alert_and_throttles(monkeypatch):
    posted = []
    monkeypatch.setattr(sync_staleness, "post_ops_alert",
                        lambda **kw: posted.append(kw) or True)
    repo = FakeRepo({**FRESH, "linkedin_tofu": "2026-07-24T22:45:00+00:00"})
    sync_staleness.check_sync_staleness(repo, now=MONDAY)
    sync_staleness.check_sync_staleness(repo, now=MONDAY)  # inside the 24h gap
    assert len(posted) == 1
    assert posted[0]["severity"] == "warning"
    assert "linkedin_tofu" in posted[0]["detail"]


def test_recovery_clears_the_incident_so_next_breach_alerts(monkeypatch):
    posted = []
    monkeypatch.setattr(sync_staleness, "post_ops_alert",
                        lambda **kw: posted.append(kw) or True)
    stale = {**FRESH, "linkedin_tofu": "2026-07-24T22:45:00+00:00"}
    repo = FakeRepo(dict(stale))
    sync_staleness.check_sync_staleness(repo, now=MONDAY)      # breach #1
    repo._sync = dict(FRESH)
    sync_staleness.check_sync_staleness(repo, now=MONDAY)      # recovery
    repo._sync = dict(stale)
    sync_staleness.check_sync_staleness(repo, now=MONDAY)      # breach #2
    assert len(posted) == 2


def test_naive_timestamps_treated_as_utc():
    """Writers stamp UTC; a naive stamp must not look 4h stale-r in ET."""
    repo = FakeRepo({**FRESH, "linkedin_tofu": "2026-07-27 13:45:00"})
    rows = sync_staleness.compute_staleness(repo, now=MONDAY)
    assert not _row(rows, "linkedin_tofu")["breached"]
