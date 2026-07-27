"""SalesforceClient transport + SOQL construction guards.

The SOQL half pins the escaping contract: every dynamic value that reaches a
query goes through ONE helper, so the next person who adds a WHERE clause
cannot quietly hand-roll (or forget) the escape. `lead_exists` takes its email
straight from LinkedIn lead-gen form payloads — external, attacker-supplied
input running against the production org.
"""

from __future__ import annotations

import pytest

from auto_search.engagement import sfdc_client as sfdc_mod
from auto_search.engagement.sfdc_client import SalesforceClient, soql_quote


class _HttpError(Exception):
    pass


class _Resp:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise _HttpError(str(self.status_code))


def _token(value="T1"):
    return _Resp(200, {"access_token": value, "instance_url": "https://inst"})


class _FakeHttp:
    """Scripted transport: pops one canned response per request."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method, url, *, params=None, data=None, json=None, headers=None):
        self.calls.append((method, url, dict(headers or {})))
        return self.script.pop(0)

    def gets(self):
        return [c for c in self.calls if c[0] == "GET"]


def _client(http):
    return SalesforceClient(client_id="cid", client_secret="sec",
                            login_url="https://login", http=http)


@pytest.fixture
def no_sleep(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(sfdc_mod.time, "sleep", slept.append)
    return slept


def _captured_queries():
    c = SalesforceClient.__new__(SalesforceClient)   # no creds needed
    seen: list[str] = []
    c.query = lambda q: seen.append(q) or iter(())   # type: ignore[method-assign]
    return c, seen


# ── the helper ───────────────────────────────────────────────────────────


def test_soql_quote_escapes_backslash_before_quote():
    assert soql_quote("O'Brien") == "O\\'Brien"
    assert soql_quote("a\\b") == "a\\\\b"
    # order matters: escaping the quote first would leave the backslash we just
    # introduced unescaped, re-opening the literal.
    assert soql_quote("a\\'b") == "a\\\\\\'b"


def test_soql_quote_strips_control_characters():
    """A raw newline/NUL inside a string literal is never legitimate data and
    breaks the query (or hides payload) — drop them."""
    assert soql_quote("a\nb\r\tc\x00d") == "abcd"


def test_soql_quote_tolerates_none_and_non_strings():
    assert soql_quote(None) == ""
    assert soql_quote(42) == "42"


# ── every dynamic value routes through it ────────────────────────────────


def test_lead_exists_escapes_the_external_email():
    c, seen = _captured_queries()
    c.lead_exists("x' OR Name != 'a")
    assert len(seen) == 1
    # the injected quote is escaped, so the literal never closes early
    assert "Email = 'x\\' OR Name != \\'a'" in seen[0]


def test_lead_exists_short_circuits_on_blank_email():
    c, seen = _captured_queries()
    assert c.lead_exists("") is False
    assert c.lead_exists("   ") is False
    assert seen == []                     # no query at all


def test_lead_source_list_is_escaped_including_backslashes(monkeypatch):
    """The IN-list built the escape by hand and skipped backslashes entirely —
    a source name containing one would have escaped to a broken literal."""
    c, seen = _captured_queries()
    c.HIGH_INTENT_SOURCES = ("Weird\\Source", "O'Neil Form")
    list(c.iter_high_intent_leads(since="2026-01-01"))
    assert "'Weird\\\\Source'" in seen[0]
    assert "'O\\'Neil Form'" in seen[0]


# ── transport resilience ─────────────────────────────────────────────────


def test_query_retries_a_transient_5xx(no_sleep):
    """A 2-minute instance maintenance 503 used to fail the whole SFDC leg —
    BOFU heat a day late plus a daily-cron FAILED page for a transient."""
    http = _FakeHttp([_token(), _Resp(503),
                      _Resp(200, {"records": [{"Id": "L1"}], "done": True})])
    rows = list(_client(http).query("SELECT Id FROM Lead"))
    assert [r["Id"] for r in rows] == ["L1"]
    assert len(http.gets()) == 2
    assert len(no_sleep) == 1


def test_query_honours_retry_after_on_429(no_sleep):
    http = _FakeHttp([_token(), _Resp(429, headers={"Retry-After": "7"}),
                      _Resp(200, {"records": [], "done": True})])
    list(_client(http).query("SELECT Id FROM Lead"))
    assert no_sleep == [7.0]


def test_query_retry_is_bounded_then_raises(no_sleep):
    """Two retries, not forever: a genuinely down instance still fails fast."""
    http = _FakeHttp([_token(), _Resp(500), _Resp(500), _Resp(500)])
    with pytest.raises(_HttpError):
        list(_client(http).query("SELECT Id FROM Lead"))
    assert len(http.gets()) == 3          # initial + 2 retries
    assert len(no_sleep) == 2


def test_query_reauthenticates_once_on_401(no_sleep):
    """A token expiring mid-pull turned every remaining page into a 401 that
    aborted the leg; now the cached token is refreshed and the page replayed."""
    http = _FakeHttp([_token("T1"), _Resp(401), _token("T2"),
                      _Resp(200, {"records": [{"Id": "L9"}], "done": True})])
    rows = list(_client(http).query("SELECT Id FROM Lead"))
    assert [r["Id"] for r in rows] == ["L9"]
    assert http.gets()[-1][2]["Authorization"] == "Bearer T2"
    assert no_sleep == []                 # a re-auth is not a backoff


def test_persistent_401_is_not_an_infinite_reauth_loop(no_sleep):
    http = _FakeHttp([_token("T1"), _Resp(401), _token("T2"), _Resp(401)])
    with pytest.raises(_HttpError):
        list(_client(http).query("SELECT Id FROM Lead"))
    assert len(http.gets()) == 2          # exactly one re-auth replay


def test_writes_are_never_retried(no_sleep):
    """create_lead is a WRITE — a replayed POST duplicates a Lead."""
    http = _FakeHttp([_token(), _Resp(500)])
    with pytest.raises(_HttpError):
        _client(http).create_lead({"LastName": "X", "Company": "Y"})
    assert len([c for c in http.calls if c[0] == "POST"]) == 2   # token + the one write
    assert no_sleep == []
