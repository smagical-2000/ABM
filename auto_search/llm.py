"""Shared Claude helpers — one place for web-search calls + JSON extraction.

Both the qualifier (classify a company) and the leadership-changes connector
(detect recent appointments) need the same three things from Claude:
    1. a call with the web_search tool + retry on transient errors
    2. extraction of the final text from a multi-block tool response
    3. robust extraction of the JSON object Claude returns

Keeping these here means there's exactly one implementation to test and
harden, instead of the same retry/parse logic copy-pasted per connector.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
from typing import Any

from anthropic import (
    APIConnectionError,
    APITimeoutError,
    AsyncAnthropic,
    InternalServerError,
    RateLimitError,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
_WEB_SEARCH_TOOL_TYPE = "web_search_20260209"

# Retry policy. Rate-limit (429) errors are handled differently from other
# transient errors: on a 429 we WAIT OUT the per-minute window (honoring the
# server's Retry-After header when present) and keep retrying, so a
# qualification resolves to a real answer instead of erroring. Other transient
# errors use a shorter exponential backoff.
_TRANSIENT = (APIConnectionError, APITimeoutError, InternalServerError)
_MAX_RETRIES = 8
_INITIAL_BACKOFF_S = 1.0
_BACKOFF_MULT = 2.0
_RATE_LIMIT_MAX_WAIT_S = 90.0

_client: AsyncAnthropic | None = None


def get_client() -> AsyncAnthropic:
    """Return a lazily-created shared Anthropic client.

    Module-level singleton is fine for the CLI / cron paths. A long-lived
    worker pool should manage lifecycle explicitly (see KNOWN_ISSUES.md).
    """
    global _client
    if _client is None:
        key = os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY not set in .env")
        _client = AsyncAnthropic(api_key=key)
    return _client


def _retry_after_seconds(err: Exception) -> float | None:
    """Seconds to wait from a 429's Retry-After header, if the SDK exposed it."""
    resp = getattr(err, "response", None)
    if resp is None:
        return None
    try:
        ra = resp.headers.get("retry-after")
        return float(ra) if ra else None
    except (TypeError, ValueError, AttributeError):
        return None


async def _create_with_retries(make_call):
    """Run an Anthropic create() call, retrying on rate-limit + transient errors.

    A 429 is waited out (Retry-After header, else a long capped backoff) and
    retried, so the call keeps going until it gets a real answer rather than
    abandoning the company as an error. Non-retryable errors propagate.
    """
    backoff = _INITIAL_BACKOFF_S
    for attempt in range(_MAX_RETRIES):
        try:
            return await make_call()
        except RateLimitError as e:
            if attempt == _MAX_RETRIES - 1:
                raise
            wait = _retry_after_seconds(e) or min(backoff * 4, _RATE_LIMIT_MAX_WAIT_S)
            wait += random.uniform(0, 1.5)
            logger.warning(
                "Claude rate limited — waiting %.0fs then retrying (%d/%d)",
                wait, attempt + 1, _MAX_RETRIES,
            )
            await asyncio.sleep(wait)
            backoff *= _BACKOFF_MULT
        except _TRANSIENT as e:
            if attempt == _MAX_RETRIES - 1:
                raise
            wait = backoff + random.uniform(0, backoff * 0.25)
            logger.warning(
                "Claude transient error (%s) — retrying in %.1fs (%d/%d)",
                type(e).__name__, wait, attempt + 1, _MAX_RETRIES,
            )
            await asyncio.sleep(wait)
            backoff *= _BACKOFF_MULT


# Sonnet 4.5 pricing per million tokens (USD), plus the web_search tool fee.
# Used only to report what each call cost — keep in sync with Anthropic pricing.
_PRICE_IN = 3.0
_PRICE_OUT = 15.0
_PRICE_CACHE_WRITE = 3.75
_PRICE_CACHE_READ = 0.30
_PRICE_PER_SEARCH = 0.01


def _cached_system(system: str) -> list[dict]:
    """Wrap the system prompt as a cacheable block.

    The rubric/system prompt is identical across accounts of the same segment,
    so caching it (5-minute TTL) makes every call after the first in a batch
    read it at ~10% cost. No effect on output — same prompt. Anthropic ignores
    the cache when the prefix is under the minimum, so this is always safe.
    """
    return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]


def call_cost(response: Any, *, searches: int = 0) -> float:
    """Best-effort USD cost of one call, from the response usage + search count.

    Server-side web_search runs inside one create() call, so the usage already
    aggregates every internal turn's tokens.
    """
    u = getattr(response, "usage", None)
    if u is None:
        return 0.0
    def g(name: str) -> int:
        return getattr(u, name, 0) or 0
    cost = (
        g("input_tokens") * _PRICE_IN
        + g("output_tokens") * _PRICE_OUT
        + g("cache_creation_input_tokens") * _PRICE_CACHE_WRITE
        + g("cache_read_input_tokens") * _PRICE_CACHE_READ
    ) / 1_000_000 + searches * _PRICE_PER_SEARCH
    return round(cost, 4)


async def call_with_web_search(
    *,
    system: str,
    user_message: str,
    max_searches: int,
    max_tokens: int,
    model: str | None = None,
    temperature: float | None = None,
) -> Any:
    """Call Claude with the web_search tool, retrying transient + rate limits.

    Returns the raw Anthropic response (multi-block: text + tool-use +
    tool-result). Use extract_text() / extract_web_searches() to read it.
    Non-retryable errors (e.g. BadRequest) propagate to the caller.

    temperature=0 makes the model deterministic, so re-scoring the same account
    returns the same answer (the only residual variance is what web_search
    itself returns on the day).
    """
    extra = {} if temperature is None else {"temperature": temperature}
    return await _create_with_retries(lambda: get_client().messages.create(
        model=model or DEFAULT_MODEL,
        max_tokens=max_tokens,
        system=_cached_system(system),
        tools=[{
            "type": _WEB_SEARCH_TOOL_TYPE,
            "name": "web_search",
            "max_uses": max_searches,
        }],
        messages=[{"role": "user", "content": user_message}],
        **extra,
    ))


async def call_plain(
    *,
    system: str,
    user_message: str,
    max_tokens: int,
    model: str | None = None,
    temperature: float | None = None,
) -> Any:
    """Call Claude with NO tools (no web_search), retrying transient failures.

    For cheap, self-contained classification where the model already has all
    the context it needs in the prompt (e.g. judging a job posting from its
    title + description). Much cheaper/faster than the web_search path.
    """
    extra = {} if temperature is None else {"temperature": temperature}
    return await _create_with_retries(lambda: get_client().messages.create(
        model=model or DEFAULT_MODEL,
        max_tokens=max_tokens,
        system=_cached_system(system),
        messages=[{"role": "user", "content": user_message}],
        **extra,
    ))


def extract_text(response: Any) -> str:
    """Concatenate the text blocks of a Claude response.

    Tool responses interleave text, server_tool_use, and tool_result blocks;
    we want only Claude's own text (its final answer).
    """
    parts: list[str] = []
    for block in response.content:
        if _block_attr(block, "type") == "text":
            text = _block_attr(block, "text")
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def extract_web_searches(response: Any) -> list[str]:
    """Return the search queries Claude ran (for logging + traces)."""
    queries: list[str] = []
    for block in response.content:
        if _block_attr(block, "type") != "server_tool_use":
            continue
        if _block_attr(block, "name") != "web_search":
            continue
        inp = _block_attr(block, "input") or {}
        q = inp.get("query") if isinstance(inp, dict) else None
        if q:
            queries.append(str(q))
    return queries


def parse_json_object(text: str) -> dict:
    """Extract and parse the first JSON object in `text`.

    Tries the whole string first, then a brace-balanced scan (string- and
    escape-aware) so nested objects and braces inside string values don't
    corrupt extraction the way a naive regex would. Raises ValueError
    (which json.JSONDecodeError subclasses) on failure.
    """
    if not text or not text.strip():
        raise ValueError("empty text")

    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    unfenced = re.sub(r"```(?:json)?|```", "", stripped)
    obj = _first_balanced_object(unfenced)
    if obj is None:
        raise ValueError(f"no balanced JSON object found in: {stripped[:200]!r}")
    return json.loads(obj)


def parse_json_array(text: str) -> list:
    """Extract and parse the first JSON array in `text`.

    Mirror of parse_json_object for connectors that return a list of rows
    (e.g. several leadership changes in one response).
    """
    if not text or not text.strip():
        raise ValueError("empty text")

    stripped = text.strip()
    try:
        result = json.loads(stripped)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    unfenced = re.sub(r"```(?:json)?|```", "", stripped)
    arr = _first_balanced(unfenced, open_ch="[", close_ch="]")
    if arr is None:
        raise ValueError(f"no JSON array found in: {stripped[:200]!r}")
    return json.loads(arr)


# ── internals ─────────────────────────────────────────────────────────


def _block_attr(block: Any, name: str) -> Any:
    """SDK content blocks expose attrs; dict-form falls back to .get()."""
    val = getattr(block, name, None)
    if val is not None:
        return val
    if isinstance(block, dict):
        return block.get(name)
    return None


def _first_balanced_object(text: str) -> str | None:
    return _first_balanced(text, open_ch="{", close_ch="}")


def _first_balanced(text: str, *, open_ch: str, close_ch: str) -> str | None:
    """Return the first balanced open_ch…close_ch substring, or None.

    String- and escape-aware: delimiters inside JSON string literals don't
    affect the depth count.
    """
    start = text.find(open_ch)
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None
