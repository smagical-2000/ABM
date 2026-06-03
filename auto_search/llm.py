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

# Transient errors worth retrying with exponential backoff.
_RETRYABLE = (RateLimitError, APIConnectionError, APITimeoutError, InternalServerError)
_MAX_RETRIES = 4
_INITIAL_BACKOFF_S = 1.0
_BACKOFF_MULT = 2.0

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


async def call_with_web_search(
    *,
    system: str,
    user_message: str,
    max_searches: int,
    max_tokens: int,
    model: str | None = None,
) -> Any:
    """Call Claude with the web_search tool, retrying transient failures.

    Returns the raw Anthropic response (multi-block: text + tool-use +
    tool-result). Use extract_text() / extract_web_searches() to read it.
    Non-retryable errors (e.g. BadRequest) propagate to the caller.
    """
    backoff = _INITIAL_BACKOFF_S
    for attempt in range(_MAX_RETRIES):
        try:
            return await get_client().messages.create(
                model=model or DEFAULT_MODEL,
                max_tokens=max_tokens,
                system=system,
                tools=[{
                    "type": _WEB_SEARCH_TOOL_TYPE,
                    "name": "web_search",
                    "max_uses": max_searches,
                }],
                messages=[{"role": "user", "content": user_message}],
            )
        except _RETRYABLE as e:
            if attempt == _MAX_RETRIES - 1:
                raise
            sleep_for = backoff + random.uniform(0, backoff * 0.25)
            logger.warning(
                "Claude transient error (%s) attempt %d/%d — sleeping %.1fs",
                type(e).__name__, attempt + 1, _MAX_RETRIES, sleep_for,
            )
            await asyncio.sleep(sleep_for)
            backoff *= _BACKOFF_MULT


async def call_plain(
    *,
    system: str,
    user_message: str,
    max_tokens: int,
    model: str | None = None,
) -> Any:
    """Call Claude with NO tools (no web_search), retrying transient failures.

    For cheap, self-contained classification where the model already has all
    the context it needs in the prompt (e.g. judging a job posting from its
    title + description). Much cheaper/faster than the web_search path.
    """
    backoff = _INITIAL_BACKOFF_S
    for attempt in range(_MAX_RETRIES):
        try:
            return await get_client().messages.create(
                model=model or DEFAULT_MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_message}],
            )
        except _RETRYABLE as e:
            if attempt == _MAX_RETRIES - 1:
                raise
            sleep_for = backoff + random.uniform(0, backoff * 0.25)
            logger.warning(
                "Claude transient error (%s) attempt %d/%d — sleeping %.1fs",
                type(e).__name__, attempt + 1, _MAX_RETRIES, sleep_for,
            )
            await asyncio.sleep(sleep_for)
            backoff *= _BACKOFF_MULT


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
