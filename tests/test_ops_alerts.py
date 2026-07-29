"""Ops alerting — the contract born from the 2026-07-07 silent cron crash:
failures are TOLD to Slack (throttled, with recovery), and the watchdog turns
a cron's silence into an alert. All calendar math and state logic is pure."""

from datetime import UTC, datetime

from auto_search.ops import alerts, watchdog


class FakeRepo:
    def __init__(self):
        self.store = {}

    def get_setting(self, key):
        return self.store.get(key)

    def set_setting(self, key, value):
        self.store[key] = value


# ── alert card + poster ───────────────────────────────────────────────


def test_alert_card_contents():
    card = alerts.build_alert_card(
        kind="daily-cron", title="Daily run FAILED: social",
        detail="TypeError: expected string, got dict", service="discovery-cron",
        build="fix-123", severity="failure",
        now=datetime(2026, 7, 7, 14, 16, tzinfo=UTC))
    text = str(card)
    for needle in ("[ALERT] Daily run FAILED: social", "TypeError",
                   "discovery-cron", "build fix-123", "Jul 07, 14:16 UTC"):
        assert needle in text
    rec = str(alerts.build_alert_card(kind="x", title="Back", severity="recovered"))
    assert "[RECOVERED] Back" in rec


def test_poster_without_webhook_is_safe(monkeypatch):
    monkeypatch.delenv("SLACK_OPS_ALERTS_WEBHOOK", raising=False)
    monkeypatch.delenv("SLACK_ENGAGEMENT_WEBHOOK", raising=False)
    assert alerts.post_ops_alert(kind="x", title="t") is False


def test_poster_falls_back_to_private_webhook_and_never_raises(monkeypatch):
    monkeypatch.delenv("SLACK_OPS_ALERTS_WEBHOOK", raising=False)
    monkeypatch.setenv("SLACK_ENGAGEMENT_WEBHOOK", "https://hooks.example/private")
    seen = {}

    class _R:
        def raise_for_status(self):
            pass

    monkeypatch.setattr(alerts.httpx, "post",
                        lambda url, json=None, timeout=None: seen.update(url=url) or _R())
    assert alerts.post_ops_alert(kind="x", title="t") is True
    assert seen["url"] == "https://hooks.example/private"

    def boom(*a, **k):
        raise RuntimeError("slack down")
    monkeypatch.setattr(alerts.httpx, "post", boom)
    assert alerts.post_ops_alert(kind="x", title="t") is False   # never raises


# ── throttle + recovery state ─────────────────────────────────────────


def test_should_alert_throttles_then_reopens():
    repo = FakeRepo()
    t0 = datetime(2026, 7, 7, 10, 0, tzinfo=UTC)
    assert alerts.should_alert(repo, "tofu-cron", min_gap_hours=3, now=t0) is True
    # 15-min cron keeps failing — inside the gap, stay quiet
    t1 = t0.replace(hour=11)
    assert alerts.should_alert(repo, "tofu-cron", min_gap_hours=3, now=t1) is False
    # past the gap — re-alert
    t2 = t0.replace(hour=13, minute=1)
    assert alerts.should_alert(repo, "tofu-cron", min_gap_hours=3, now=t2) is True


def test_mark_ok_fires_exactly_once():
    repo = FakeRepo()
    alerts.should_alert(repo, "daily-cron", min_gap_hours=0)
    assert alerts.mark_ok(repo, "daily-cron") is True    # the one recovery
    assert alerts.mark_ok(repo, "daily-cron") is False   # nothing open now


def test_state_errors_fail_open():
    """A broken settings store must over-alert, never go silent."""
    class BrokenRepo:
        def get_setting(self, key):
            raise RuntimeError("db down")

        def set_setting(self, key, value):
            raise RuntimeError("db down")

    assert alerts.should_alert(BrokenRepo(), "x") is True


# ── watchdog calendar math ────────────────────────────────────────────


def _dt(day, hour, minute=0):
    return datetime(2026, 7, day, hour, minute, tzinfo=UTC)   # Jul 6 2026 = Monday


def test_daily_fresh_run_is_ok():
    assert watchdog.overdue_daily(_dt(7, 14, 20).isoformat(), _dt(7, 16, 30)) is None


def test_daily_missed_run_is_overdue():
    # last success yesterday; today's 12:30 slot + 2h grace has passed
    reason = watchdog.overdue_daily(_dt(6, 14, 5).isoformat(), _dt(7, 16, 30))
    assert reason and "expected one at Jul 07 12:30" in reason


def test_daily_never_ran_is_overdue():
    assert watchdog.overdue_daily(None, _dt(7, 16, 30))


def test_daily_grace_and_weekend_are_quiet():
    # Monday 14:00 is inside the 2h grace after the 12:30 slot — expected slot
    # is FRIDAY'S, and Friday's success covers it
    assert watchdog.overdue_daily(_dt(3, 14, 10).isoformat(), _dt(6, 14, 0)) is None
    # Saturday: Friday's run covers the whole weekend
    assert watchdog.overdue_daily(_dt(3, 14, 10).isoformat(), _dt(4, 18, 0)) is None


def test_daily_early_same_day_success_is_ok_all_day():
    """The 2026-07-28 double false alarm, exactly: the cron runs at 12:30 UTC
    (railway.cron.json `30 12 * * 1-5`) and stamped green at 12:56 — BEFORE the
    watchdog's stale 14:00 expectation — so the 16:07 and 22:16 passes both
    paged "Daily discovery cron did not run" on a day that had already run.
    A same-day green stamp satisfies the whole day, wherever it lands."""
    ok = datetime(2026, 7, 28, 12, 56, tzinfo=UTC).isoformat()   # Tue Jul 28
    assert watchdog.overdue_daily(ok, datetime(2026, 7, 28, 16, 7, tzinfo=UTC)) is None
    assert watchdog.overdue_daily(ok, datetime(2026, 7, 28, 22, 16, tzinfo=UTC)) is None


def test_daily_same_day_forgiveness_ends_at_next_weekday_window():
    # keep the REAL miss: Monday's green run does not cover Tuesday once
    # Tuesday's 12:30 slot + 2h grace has passed
    ok = datetime(2026, 7, 27, 12, 56, tzinfo=UTC).isoformat()   # Mon Jul 27
    reason = watchdog.overdue_daily(ok, datetime(2026, 7, 28, 16, 7, tzinfo=UTC))
    assert reason and "expected one at Jul 28 12:30" in reason


def test_tofu_stale_only_inside_selling_hours():
    old = _dt(7, 13, 0).isoformat()
    assert watchdog.stale_tofu(old, _dt(7, 16, 0))           # 3h stale, in window
    assert watchdog.stale_tofu(old, _dt(7, 5, 0)) is None    # out of window: quiet
    assert watchdog.stale_tofu(old, _dt(4, 16, 0)) is None   # Saturday: quiet
    assert watchdog.stale_tofu(old, _dt(7, 13, 30)) is None  # inside window grace
    fresh = _dt(7, 15, 50).isoformat()
    assert watchdog.stale_tofu(fresh, _dt(7, 16, 0)) is None


def test_run_checks_alerts_once_then_recovers(monkeypatch):
    repo = FakeRepo()
    posted = []
    monkeypatch.setattr(alerts, "post_ops_alert",
                        lambda **kw: posted.append(kw) or True)
    now = _dt(7, 17, 0)
    repo.set_setting("ops_tofu_last_tick", _dt(7, 16, 55).isoformat())  # tofu fine

    first = watchdog.run_checks(repo, now=now)                # daily never ran
    assert [a["status"] for a in first if a["check"] == "daily-cron-silent"] == ["alerted"]
    second = watchdog.run_checks(repo, now=now.replace(minute=30))
    assert [a["status"] for a in second if a["check"] == "daily-cron-silent"] == ["quiet"]
    assert len(posted) == 1                                   # throttled

    repo.set_setting("ops_daily_last_ok", _dt(7, 17, 40).isoformat())
    repo.set_setting("ops_tofu_last_tick", _dt(7, 17, 55).isoformat())  # keep tofu fresh
    third = watchdog.run_checks(repo, now=now.replace(hour=18))
    assert [a["status"] for a in third if a["check"] == "daily-cron-silent"] == ["recovered"]
    assert posted[-1]["severity"] == "recovered"
    repo.set_setting("ops_tofu_last_tick", _dt(7, 18, 55).isoformat())
    fourth = watchdog.run_checks(repo, now=now.replace(hour=19))
    assert all(a["status"] == "ok" for a in fourth)           # steady state: silent
