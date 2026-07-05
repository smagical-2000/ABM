"""Auto-notify cron leg — the safety guarantees. No network: httpx.post is patched,
so a bug that would POST when it must not FAILS the test rather than spamming Slack."""

import importlib.util
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "run_engagement_notify",
    Path(__file__).resolve().parent.parent / "scripts" / "run_engagement_notify.py",
)
ren = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ren)


class _Resp:
    def __init__(self, js):
        self._js = js

    def raise_for_status(self):
        pass

    def json(self):
        return self._js


def test_disabled_by_default_never_posts(monkeypatch):
    """Kill switch: no ENGAGEMENT_NOTIFY_ENABLED=1 → no-op, and it must not touch HTTP."""
    monkeypatch.delenv("ENGAGEMENT_NOTIFY_ENABLED", raising=False)
    monkeypatch.setenv("ENGAGEMENT_APP_URL", "https://x")
    monkeypatch.setattr(sys, "argv", ["run_engagement_notify.py"])

    def boom(*_a, **_k):
        raise AssertionError("must NOT POST when the kill switch is off")

    monkeypatch.setattr(ren.httpx, "post", boom)
    assert ren.main() == 0


def test_dry_run_works_while_disabled_and_sends_nothing(monkeypatch):
    """--dry-run bypasses the kill switch (safe test path) and asks for dry_run only."""
    monkeypatch.delenv("ENGAGEMENT_NOTIFY_ENABLED", raising=False)  # still disabled
    monkeypatch.setenv("ENGAGEMENT_APP_URL", "https://x")
    monkeypatch.setattr(sys, "argv", ["run_engagement_notify.py", "--dry-run"])
    seen = {}

    def spy(_url, params=None, **_k):
        seen.update(params or {})
        return _Resp({"due": 3})

    monkeypatch.setattr(ren.httpx, "post", spy)
    assert ren.main() == 0
    assert seen == {"dry_run": "true"}  # never sends limit → never posts


def test_send_passes_the_cap_as_limit(monkeypatch):
    """Enabled live send caps the endpoint at ENGAGEMENT_NOTIFY_MAX (circuit breaker)."""
    monkeypatch.setenv("ENGAGEMENT_NOTIFY_ENABLED", "1")
    monkeypatch.setenv("ENGAGEMENT_APP_URL", "https://x")
    monkeypatch.setenv("ENGAGEMENT_NOTIFY_MAX", "7")
    monkeypatch.setattr(sys, "argv", ["run_engagement_notify.py"])
    seen = {}

    def spy(_url, params=None, **_k):
        seen.update(params or {})
        return _Resp({"due": 2, "posted": 2, "live": True})

    monkeypatch.setattr(ren.httpx, "post", spy)
    assert ren.main() == 0
    assert seen == {"limit": "7"}


def test_request_failure_is_non_fatal_returns_1(monkeypatch):
    """A Slack/endpoint failure returns rc=1 (logged) but never raises into run_daily."""
    monkeypatch.setenv("ENGAGEMENT_NOTIFY_ENABLED", "1")
    monkeypatch.setenv("ENGAGEMENT_APP_URL", "https://x")
    monkeypatch.setattr(sys, "argv", ["run_engagement_notify.py"])

    def boom(*_a, **_k):
        raise ren.httpx.ConnectError("down")

    monkeypatch.setattr(ren.httpx, "post", boom)
    assert ren.main() == 1


def test_no_app_url_is_a_no_op(monkeypatch):
    monkeypatch.setenv("ENGAGEMENT_NOTIFY_ENABLED", "1")
    monkeypatch.delenv("ENGAGEMENT_APP_URL", raising=False)
    monkeypatch.setattr(sys, "argv", ["run_engagement_notify.py"])

    def boom(*_a, **_k):
        raise AssertionError("must NOT POST without ENGAGEMENT_APP_URL")

    monkeypatch.setattr(ren.httpx, "post", boom)
    assert ren.main() == 0
