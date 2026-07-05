"""Event-driven notify push from the TOFU runner + the Hot-always seed/reset rule.

Runner: the notify push fires ONLY when a real run wrote new engagement events
(condition hits -> we push); the active-hours gate skips before any spend.
Endpoint: seeding can never pre-suppress Hot (baseline caps at Warm), and
hot_reset re-arms previously-seeded Hot ledgers except already-sent accounts."""

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_SPEC = importlib.util.spec_from_file_location(
    "run_linkedin_tofu",
    Path(__file__).resolve().parent.parent / "scripts" / "run_linkedin_tofu.py",
)
rlt = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rlt)


# ── active-hours cost gate ────────────────────────────────────────────


def _at(hour, weekday=2):     # weekday 2 = Wednesday
    d = datetime(2026, 7, 1 + weekday - 2, hour, 30, tzinfo=UTC)
    assert d.weekday() == weekday
    return d


def test_hours_gate_unset_is_always_active(monkeypatch):
    monkeypatch.delenv("LINKEDIN_TOFU_ACTIVE_HOURS_UTC", raising=False)
    monkeypatch.delenv("LINKEDIN_TOFU_WEEKDAYS_ONLY", raising=False)
    assert rlt._within_active_hours(_at(3)) is True


def test_hours_gate_window(monkeypatch):
    monkeypatch.setenv("LINKEDIN_TOFU_ACTIVE_HOURS_UTC", "13-23")
    assert rlt._within_active_hours(_at(13)) is True     # start inclusive
    assert rlt._within_active_hours(_at(22)) is True
    assert rlt._within_active_hours(_at(23)) is False    # end exclusive
    assert rlt._within_active_hours(_at(3)) is False


def test_hours_gate_weekend(monkeypatch):
    monkeypatch.setenv("LINKEDIN_TOFU_ACTIVE_HOURS_UTC", "13-23")
    monkeypatch.setenv("LINKEDIN_TOFU_WEEKDAYS_ONLY", "1")
    assert rlt._within_active_hours(_at(15, weekday=5)) is False   # Saturday
    assert rlt._within_active_hours(_at(15, weekday=2)) is True


def test_hours_gate_bad_value_fails_open(monkeypatch):
    monkeypatch.setenv("LINKEDIN_TOFU_ACTIVE_HOURS_UTC", "nonsense")
    assert rlt._within_active_hours(_at(3)) is True


# ── notify push fires only on new events ──────────────────────────────


class _FakeEngRepo:
    def ensure_schema(self):
        pass

    def get_sync_state(self, source=None):
        return {}

    def set_sync_state(self, **kw):
        pass


def _run_main(monkeypatch, heat_events, pushed):
    monkeypatch.setenv("LINKEDIN_TOFU_CRON_ENABLED", "1")   # live path, stubbed clients

    async def fake_run(**_kw):
        return {"stats": {"heat_events": heat_events}}
    monkeypatch.setattr(rlt.linkedin_ads_runner, "run", fake_run)
    monkeypatch.setattr(rlt.linkedin_ads, "load_share_categories",
                        lambda _t: {"1": "Ortho"})
    monkeypatch.setattr(rlt, "get_engagement_repository", lambda: _FakeEngRepo())
    monkeypatch.setattr(rlt, "get_scoring_repository", lambda: None)
    monkeypatch.setattr(rlt, "get_repository", lambda: None)
    monkeypatch.setattr(rlt.subprocess, "run",
                        lambda cmd, **_k: pushed.append(cmd) or type("R", (), {"returncode": 0})())
    monkeypatch.setattr(rlt.sys, "argv", ["run_linkedin_tofu.py", "--force", "--dry-run"])
    # dry-run skips clients + stamping; force bypasses gates. But dry-run also
    # skips the push — so run live-shaped with clients stubbed instead:
    monkeypatch.setattr(rlt.sys, "argv", ["run_linkedin_tofu.py", "--force"])
    import types

    fake_mod = types.SimpleNamespace(AirtableClient=lambda: None)
    fake_reply = types.SimpleNamespace(ReplyioClient=lambda: None)
    monkeypatch.setitem(rlt.sys.modules, "auto_search.engagement.airtable_client", fake_mod)
    monkeypatch.setitem(rlt.sys.modules, "auto_search.engagement.replyio_client", fake_reply)
    return rlt.main()


def test_push_fires_when_events_landed(monkeypatch):
    pushed = []
    assert _run_main(monkeypatch, heat_events=2, pushed=pushed) == 0
    assert len(pushed) == 1
    assert "run_engagement_notify.py" in str(pushed[0][1])


def test_no_push_when_nothing_landed(monkeypatch):
    pushed = []
    assert _run_main(monkeypatch, heat_events=0, pushed=pushed) == 0
    assert pushed == []


# ── Hot reactivation: pure rule (notify.accounts_to_notify) ───────────


def _acct(aid, tier, touch=None):
    return {"account_id": aid, "tier": tier, "last_touch": touch}


def test_hot_reactivates_on_newer_touch_only():
    from auto_search.engagement import notify
    accounts = [
        _acct("stable_hot", "Hot", "2026-07-01T10:00:00+00:00"),   # no new touch → silent
        _acct("active_hot", "Hot", "2026-07-05T12:00:00+00:00"),   # newer touch → re-fire
        _acct("rose_hot", "Hot", "2026-07-05T09:00:00+00:00"),     # Warm→Hot → fire (rose)
        _acct("warm_again", "Warm", "2026-07-05T09:00:00+00:00"),  # same tier → silent
    ]
    ledger = {
        "stable_hot": {"tier": "Hot", "touch": "2026-07-01T10:00:00+00:00"},
        "active_hot": {"tier": "Hot", "touch": "2026-07-03T10:00:00+00:00"},
        "rose_hot": {"tier": "Warm", "touch": "2026-07-04T10:00:00+00:00"},
        "warm_again": {"tier": "Warm", "touch": "2026-07-01T10:00:00+00:00"},
    }
    got = {d["account"]["account_id"]: d["reason"]
           for d in notify.accounts_to_notify(accounts, ledger)}
    assert got == {"active_hot": "hot_activity", "rose_hot": "rose"}


def test_missing_baseline_touch_never_backfires():
    """A legacy bare-string ledger (no touch) must NOT re-fire a Hot account —
    the guard against draining the whole backlog on the first post-deploy run."""
    from auto_search.engagement import notify
    accounts = [_acct("old_hot", "Hot", "2026-07-05T12:00:00+00:00")]
    assert notify.accounts_to_notify(accounts, {"old_hot": "Hot"}) == []


def test_mixed_utc_offsets_compare_correctly():
    from auto_search.engagement import notify
    a = [_acct("x", "Hot", "2026-07-05T09:00:00-04:00")]        # = 13:00Z, newer
    led = {"x": {"tier": "Hot", "touch": "2026-07-05T12:30:00+00:00"}}
    assert len(notify.accounts_to_notify(a, led)) == 1


# ── endpoint: seed baselines to now + Hot reactivation end-to-end ─────


@pytest.fixture
def client(tmp_path, monkeypatch):
    import importlib

    _app_module = importlib.import_module("auto_search.api.app")
    from auto_search.db.repository import JsonFileRepository
    from auto_search.db.scoring_repository import ScoringJsonRepository

    monkeypatch.delenv("BASIC_AUTH_USER", raising=False)
    monkeypatch.delenv("BASIC_AUTH_PASS", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(_app_module, "get_repository",
                        lambda: JsonFileRepository(tmp_path / "s.json"))
    monkeypatch.setattr(_app_module, "get_scoring_repository",
                        lambda: ScoringJsonRepository(tmp_path / "sc.json"))
    with TestClient(_app_module.create_app()) as c:
        yield c, _app_module


def test_seed_baselines_to_now_then_nothing_fires(client):
    c, _ = client
    touch = {"v": "2026-07-04T10:00:00+00:00"}   # mutable so the test can advance it
    row = {"account_id": "abm_hot", "score": 25, "clicks": 0, "replies": 0,
           "meetings": 0, "contacts": 0, "delivered": 0, "opened": 0,
           "replied_sends": 0}
    # _engaged_view is a create_app closure; its data seam is repo.engaged_accounts.
    c.app.state.engagement_repo.engaged_accounts = lambda: [{**row, "last_touch": touch["v"]}]

    seeded = c.post("/api/engagement/notify-changes", params={"seed": "true"}).json()
    assert seeded["seeded"] == 1 and seeded["format"] == "tier+touch"
    # nothing fires right after seed (the "not like right now" guarantee)
    assert c.post("/api/engagement/notify-changes", params={"dry_run": "true"}).json()["due"] == 0
    # a NEW touch on the already-Hot account now re-fires
    touch["v"] = "2026-07-05T15:00:00+00:00"
    due = c.post("/api/engagement/notify-changes", params={"dry_run": "true"}).json()
    assert due["due"] == 1 and due["detail"][0]["reason"] == "hot_activity"
