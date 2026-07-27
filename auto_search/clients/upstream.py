"""Upstream-failure classification — the one rule that keeps an outage LOUD.

Born from 2026-07-27: the Apify account hit its monthly usage hard limit and
answered EVERY actor call with

    403 {"error": {"type": "platform-feature-disabled",
                   "message": "Monthly usage hard limit exceeded"}}

Both clients (clients/apify_jobs.py, clients/signalbase.py) and every connector
above them treated that body as "no rows": connector_runs stamped `success`,
run_discovery exited 0, and run_daily printed "all legs OK" for three days while
jobs discovery, all three SignalBase feeds, social listening and TOFU capture
were dead. An empty result and a refused request must never be the same value.

The contract: an HTTP error, or a 200 carrying an Apify-style `{"error": {...}}`
envelope, RAISES.
  • UpstreamQuotaError — the account is capped/out of credits (402/429, or a
    quota-flavoured message). One consolidated ops alert; the fix is billing,
    not code, and it is account-wide, so no caller may treat it as a per-target
    blip.
  • UpstreamError — any other refusal (auth, actor error, 5xx). Still fatal to
    the source, still recorded as a failed connector run.
"""

from __future__ import annotations

import json
from typing import Any


def apify_auth(token: str) -> dict[str, str]:
    """Apify auth as a HEADER, never `?token=`.

    httpx logs the full request URL at INFO, so a token in the query string is
    printed dozens of times per cron run into Railway's log store (2026-07-27:
    the live key was sitting in the discovery-cron and social logs). Apify
    accepts `Authorization: Bearer <token>` on every v2 endpoint — verified
    live against run-sync and run-sync-get-dataset-items."""
    return {"Authorization": f"Bearer {token}"}


class UpstreamError(RuntimeError):
    """An upstream API refused the call. NEVER convert this to an empty result."""


class UpstreamQuotaError(UpstreamError):
    """The upstream account is out of quota/credits (or hard-limited).

    Account-wide by nature: every other source on the same key is down too, so
    callers must let it propagate instead of skipping one target."""


# Statuses that are quota by definition, whatever the body says.
_QUOTA_STATUS = frozenset({402, 429})
# Substrings seen on capped Apify/SignalBase accounts (lowercased match).
_QUOTA_MARKERS = (
    "hard limit",
    "usage limit",
    "limit exceeded",
    "exceeded the limit",
    "quota",
    "platform-feature-disabled",
    "insufficient credit",
    "not enough credit",
    "out of credit",
    "payment required",
    "rate limit",
)


def _as_text(body: Any) -> str:
    if isinstance(body, str):
        return body
    try:
        return json.dumps(body)
    except (TypeError, ValueError):
        return str(body)


def has_error_envelope(body: Any) -> bool:
    """True for Apify's `{"error": {...}}` shape — the tell that a 200-looking
    body is actually a refusal."""
    return isinstance(body, dict) and bool(body.get("error"))


def is_quota(status_code: int, body: Any) -> bool:
    """Is this refusal a billing/quota cap rather than a one-off error?"""
    if status_code in _QUOTA_STATUS:
        return True
    text = _as_text(body).lower()
    return any(m in text for m in _QUOTA_MARKERS)


def raise_for_upstream(source: str, status_code: int, body: Any) -> None:
    """No-op for a usable response; raise UpstreamQuotaError/UpstreamError for a
    refusal. `body` is the parsed JSON when available, else the raw text."""
    if status_code < 400 and not has_error_envelope(body):
        return
    detail = _as_text(body)[:300]
    cls = UpstreamQuotaError if is_quota(status_code, body) else UpstreamError
    raise cls(f"{source} → HTTP {status_code}: {detail}")
