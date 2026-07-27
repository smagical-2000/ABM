"""SalesforceClient transport + SOQL construction guards.

The SOQL half pins the escaping contract: every dynamic value that reaches a
query goes through ONE helper, so the next person who adds a WHERE clause
cannot quietly hand-roll (or forget) the escape. `lead_exists` takes its email
straight from LinkedIn lead-gen form payloads — external, attacker-supplied
input running against the production org.
"""

from __future__ import annotations

from auto_search.engagement.sfdc_client import SalesforceClient, soql_quote


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
