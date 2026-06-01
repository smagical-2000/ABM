"""ICP qualification — website-based deep evaluation via Claude + web_search.

For every signal that passes the structural pre-filter (US + scale), we ask
Claude to:
    1. Use the web_search tool to find the company's actual website
    2. Read their about / services / who-we-serve pages
    3. Classify them against Magical's ICP
    4. Return a structured verdict with the URL it relied on

Why this over keyword matching:
    Industry labels on layoff trackers are unreliable. "Healthcare" might
    actually be a wellness app, "Other" might be a hospital system. The
    website is the ground truth.

Cost ~ $0.05–0.15 per company on Sonnet 4.5 with web_search.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from anthropic import (
    APIConnectionError,
    APITimeoutError,
    AsyncAnthropic,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)
from dotenv import load_dotenv

from auto_search.models import MIN_LAID_OFF, QualificationResult, RawSignal
from auto_search.normalize import slugify

load_dotenv()
logger = logging.getLogger(__name__)

_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
_WEB_SEARCH_MAX_USES = 6        # cap searches per evaluation (cost guardrail)
_MAX_TOKENS          = 1200
_CONFIDENCE_FLOOR    = 0.70

# Per-call trace dir — every qualification writes a JSON file here so you
# can read exactly what Claude saw, searched for, and concluded. Critical
# for debugging prompt issues without re-running expensive LLM calls.
_TRACE_DIR = Path(os.getenv("QUALIFIER_TRACE_DIR", "./data/qualifier_traces"))

# Retry config for Claude calls
_LLM_MAX_RETRIES       = 4
_LLM_INITIAL_BACKOFF_S = 1.0
_LLM_BACKOFF_MULT      = 2.0

# Country values treated as US for the structural pre-filter. Empty is
# allowed because WARN data carries no country field (US-only by statute).
_US_COUNTRY_VALUES = {"", "usa", "us", "united states", "u.s.", "u.s.a."}


# ═════════════════════════════════════════════════════════════════════
# ICP PROMPT
# ═════════════════════════════════════════════════════════════════════

ICP_SYSTEM_PROMPT = textwrap.dedent("""
    You are an account researcher for Magical, an agentic-AI revenue
    cycle management (RCM) platform sold to US healthcare organizations.

    Your job: given a company that just had layoffs, determine whether
    it fits Magical's Ideal Customer Profile (ICP). Use the web_search
    tool to research the company's actual website and operations — do
    NOT guess based on the company name or the layoff snippet alone.

    ═══════════════════════════════════════════════════════════════════
    ICP — QUALIFIED if the company is one of:
    ═══════════════════════════════════════════════════════════════════

    1. SPECIALTY PRACTICE
       - Orthopedics, behavioral health, physical therapy, or
         ambulatory surgery centers (ASCs)
       - Multi-location practice group
       - ~100–5,000 employees
       - US-based

    2. PAYER
       - Medicare Advantage MCO
       - Medicaid MCO
       - Regional Blue Cross plan
       - ~500+ employees
       - US-based

    3. HEALTH SYSTEM
       - Community hospital or mid-market hospital system
       - ~1,000–50,000 employees
       - US-based
       - NOT a mega-enterprise (Mayo, Kaiser, HCA, Ascension,
         Cleveland Clinic, Providence — different motion)

    ═══════════════════════════════════════════════════════════════════
    HARD DISQUALIFIERS — set qualified = false if ANY apply:
    ═══════════════════════════════════════════════════════════════════

      • Headquartered outside the US
      • Pure tech / SaaS / digital health vendor (not a provider/payer)
      • Pharma manufacturer
      • Medical device manufacturer
      • Biotech / drug discovery
      • Dental-only practice
      • Solo or small clinic (< 100 employees)
      • Mega-enterprise health system (see list above)
      • Government agency (VA, IHS, state health dept)
      • Health insurance broker or agency (not a payer itself)
      • Consumer wellness app (fitness, meditation, mental wellness app)
      • Lab testing only (LabCorp, Quest — different motion)

    ═══════════════════════════════════════════════════════════════════
    RESEARCH PROCESS — DO THIS:
    ═══════════════════════════════════════════════════════════════════

    1. Search for the company's official website
    2. Read their About / Services / Who We Serve / Solutions page
    3. Determine:
         - Company type: provider | payer | vendor | tech | pharma | device |
                         biotech | government | consumer | other
         - Service area / specialty
         - Approximate employee count (LinkedIn, About, news)
         - HQ location
         - Whether RCM / finance ops / claims is a core function
    4. Cross-check against the layoff context for plausibility

    ═══════════════════════════════════════════════════════════════════
    OUTPUT — return ONLY this JSON object, no prose, no markdown fences:
    ═══════════════════════════════════════════════════════════════════

    {
      "qualified": true | false,
      "segment": "specialty" | "payer" | "health_system" | null,
      "sub_segment": "ortho" | "behavioral_health" | "pt" | "asc"
                   | "medicare_advantage" | "medicaid_mco" | "bcbs"
                   | "community_hospital" | "mid_market_hs" | null,
      "company_type": "provider" | "payer" | "vendor" | "tech" | "pharma"
                    | "device" | "biotech" | "government" | "consumer"
                    | "other" | "unknown",
      "approximate_employees": <int or null>,
      "confidence": <float 0.0–1.0>,
      "reasoning": "<2-3 sentences with specific evidence from the website>",
      "evidence_url": "<the URL that most informed your decision>",
      "needs_human_review": <true if confidence < 0.7 or can't access website>
    }

    Rules:
      • If you cannot access the company website at all, set
        needs_human_review=true and confidence < 0.5
      • Confidence < 0.7 → ALWAYS set needs_human_review = true
      • Be conservative — false positives waste reviewer time
      • Keep reasoning factual and specific (cite what you found)
""").strip()


# ═════════════════════════════════════════════════════════════════════
# Stage 1 — structural pre-filter (cheap, no LLM)
# ═════════════════════════════════════════════════════════════════════

def passes_rules(signal: RawSignal) -> tuple[bool, str]:
    """Structural pre-filter only. NO industry classification here —
    that's the website qualifier's job. We only drop signals that are
    structurally out of scope.
    """
    payload = signal.payload

    country = (payload.get("country") or "").lower().strip()
    if country and country not in _US_COUNTRY_VALUES:
        return False, f"non-US country: {country!r}"

    laid_off = payload.get("laid_off_count")
    if isinstance(laid_off, int) and laid_off < MIN_LAID_OFF:
        return False, f"too small (laid off {laid_off})"

    return True, "passed structural pre-filter"


# ═════════════════════════════════════════════════════════════════════
# Stage 2 — Claude with web_search
# ═════════════════════════════════════════════════════════════════════

_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        key = os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY not set in .env")
        _client = AsyncAnthropic(api_key=key)
    return _client


async def qualify_with_llm(signal: RawSignal) -> QualificationResult:
    """Send the signal to Claude with web_search enabled. Claude visits
    the company website, classifies them, returns structured JSON.

    Persists a full trace to data/qualifier_traces/<company>.json so you
    can read what Claude searched for and concluded without re-running.
    """
    context = {
        "company": signal.company_name_raw,
        "signal_type": signal.signal_type,
        "observed_at": signal.observed_at.isoformat(),
        "payload": signal.payload,
    }

    user_message = textwrap.dedent(f"""
        Evaluate the following company against Magical's ICP. Use the
        web_search tool to research their official website before deciding.

        Signal context:
        ```json
        {json.dumps(context, indent=2, default=str)}
        ```

        Respond with ONLY the JSON object — no prose, no fences.
    """).strip()

    logger.info(
        "qualifying %r via web_search (signal=%s, observed=%s)",
        signal.company_name_raw, signal.signal_type,
        signal.observed_at.date(),
    )

    response = await _call_claude_with_retry(
        system=ICP_SYSTEM_PROMPT,
        user_message=user_message,
    )

    text = _extract_final_text(response)
    web_searches = _extract_web_searches(response)
    if web_searches:
        logger.info(
            "  → %d web search(es): %s",
            len(web_searches),
            ", ".join(f"{s!r}" for s in web_searches[:3]),
        )

    # Parse → verdict
    parse_error: str | None = None
    schema_error: str | None = None
    verdict: QualificationResult

    try:
        data = _parse_json_strict(text)
    except ValueError as e:
        parse_error = str(e)
        logger.warning("qualifier got non-JSON for %s: %s",
                       signal.company_name_raw, e)
        verdict = QualificationResult(
            qualified=False,
            confidence=0.0,
            reasoning=f"LLM returned non-JSON output: {e}",
            needs_human_review=True,
            decided_by="llm",
        )
    else:
        try:
            verdict = QualificationResult(**data, decided_by="llm")
        except Exception as e:    # pydantic validation
            schema_error = f"{type(e).__name__}: {e}"
            logger.warning(
                "qualifier verdict failed schema for %s: %s  raw=%r",
                signal.company_name_raw, e, data,
            )
            verdict = QualificationResult(
                qualified=False,
                confidence=0.0,
                reasoning=f"LLM verdict did not match schema: {e}",
                needs_human_review=True,
                decided_by="llm",
            )

    # Persist trace for prompt debugging — independent of verdict success
    _write_trace(
        signal=signal,
        user_message=user_message,
        response=response,
        final_text=text,
        web_searches=web_searches,
        verdict=verdict,
        parse_error=parse_error,
        schema_error=schema_error,
    )

    # Surface verdict reasoning at INFO — visible without --verbose
    icon = "✅" if verdict.qualified else ("🟡" if verdict.needs_human_review else "❌")
    logger.info(
        "  → %s  %s  segment=%s  conf=%.2f  evidence=%s",
        icon,
        "QUALIFIED" if verdict.qualified else (
            "REVIEW" if verdict.needs_human_review else "DISQUAL"
        ),
        verdict.segment or "—",
        verdict.confidence,
        verdict.evidence_url or "—",
    )
    if verdict.reasoning:
        logger.info("  → reasoning: %s", verdict.reasoning)

    return verdict


# ═════════════════════════════════════════════════════════════════════
# Orchestrator
# ═════════════════════════════════════════════════════════════════════

async def qualify(signal: RawSignal) -> QualificationResult:
    """Two-stage qualification — structural rules → website-based LLM."""
    passed, reason = passes_rules(signal)
    if not passed:
        return QualificationResult(
            qualified=False,
            confidence=0.95,
            reasoning=f"Pre-filter disqualification: {reason}",
            needs_human_review=False,
            decided_by="rules",
        )

    try:
        result = await qualify_with_llm(signal)
    except BadRequestError as e:
        return QualificationResult(
            qualified=False,
            confidence=0.0,
            reasoning=f"LLM bad request: {e}",
            needs_human_review=True,
            decided_by="rules+llm",
        )
    except Exception as e:    # noqa: BLE001 — never crash the pipeline
        logger.error("LLM call failed for %s: %s", signal.company_name_raw, e)
        return QualificationResult(
            qualified=False,
            confidence=0.0,
            reasoning=f"LLM call failed: {type(e).__name__}: {e}",
            needs_human_review=True,
            decided_by="rules+llm",
        )

    # Confidence floor — LLM scores are not calibrated; trust but verify
    if result.confidence < _CONFIDENCE_FLOOR:
        result.needs_human_review = True

    result.decided_by = "rules+llm"
    return result


# ═════════════════════════════════════════════════════════════════════
# Anthropic helpers — retry, content extraction, JSON parsing
# ═════════════════════════════════════════════════════════════════════

async def _call_claude_with_retry(*, system: str, user_message: str):
    """Call Claude w/ web_search tool. Exponential backoff on transients."""
    backoff = _LLM_INITIAL_BACKOFF_S
    for attempt in range(_LLM_MAX_RETRIES):
        try:
            return await _get_client().messages.create(
                model=_MODEL,
                max_tokens=_MAX_TOKENS,
                system=system,
                tools=[
                    {
                        "type": "web_search_20260209",
                        "name": "web_search",
                        "max_uses": _WEB_SEARCH_MAX_USES,
                    }
                ],
                messages=[{"role": "user", "content": user_message}],
            )
        except (RateLimitError, APIConnectionError,
                APITimeoutError, InternalServerError) as e:
            if attempt == _LLM_MAX_RETRIES - 1:
                raise
            jitter = random.uniform(0, backoff * 0.25)
            sleep_for = backoff + jitter
            logger.warning(
                "Claude transient error (%s) attempt %d/%d — sleeping %.1fs",
                type(e).__name__, attempt + 1,
                _LLM_MAX_RETRIES, sleep_for,
            )
            await asyncio.sleep(sleep_for)
            backoff *= _LLM_BACKOFF_MULT


def _extract_final_text(response: Any) -> str:
    """Concatenate text blocks from a Claude response.

    With server-side tools (web_search), response.content has multiple
    blocks: text, server_tool_use, web_search_tool_result, more text.
    We want only the text blocks — Claude's final answer.
    """
    parts: list[str] = []
    for block in response.content:
        block_type = _block_attr(block, "type")
        if block_type != "text":
            continue
        text = _block_attr(block, "text")
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _extract_web_searches(response: Any) -> list[str]:
    """Pull out the search queries Claude ran (for logging + traces)."""
    queries: list[str] = []
    for block in response.content:
        if _block_attr(block, "type") != "server_tool_use":
            continue
        if _block_attr(block, "name") != "web_search":
            continue
        inp = _block_attr(block, "input") or {}
        q = (inp.get("query") if isinstance(inp, dict) else None)
        if q:
            queries.append(str(q))
    return queries


def _block_attr(block: Any, name: str) -> Any:
    """SDK content blocks expose attrs; dict-form falls back to .get()."""
    val = getattr(block, name, None)
    if val is not None:
        return val
    if isinstance(block, dict):
        return block.get(name)
    return None


def _write_trace(
    *,
    signal: RawSignal,
    user_message: str,
    response: Any,
    final_text: str,
    web_searches: list[str],
    verdict: QualificationResult,
    parse_error: str | None,
    schema_error: str | None,
) -> None:
    """Write a JSON trace per qualification so prompt failures are debuggable.

    Path: {QUALIFIER_TRACE_DIR}/{YYYY-MM-DD}/{slug}__{HHMMSS}.json

    Includes:
      - signal data sent in
      - user_message (the actual prompt body)
      - web_search queries Claude ran
      - raw text of Claude's final response
      - parsed verdict + any parse/schema errors
      - token usage if reported
    """
    try:
        _TRACE_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        day_dir = _TRACE_DIR / now.date().isoformat()
        day_dir.mkdir(exist_ok=True)

        fname = f"{slugify(signal.company_name_raw)}__{now.strftime('%H%M%S')}.json"

        usage = getattr(response, "usage", None)
        usage_data = None
        if usage:
            usage_data = {
                "input_tokens":  getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
            }

        trace = {
            "qualified_at_utc":     now.isoformat(),
            "company":              signal.company_name_raw,
            "signal_type":          signal.signal_type,
            "signal_payload":       signal.payload,
            "user_message":         user_message,
            "web_searches":         web_searches,
            "claude_final_text":    final_text,
            "verdict":              verdict.model_dump(),
            "parse_error":          parse_error,
            "schema_error":         schema_error,
            "stop_reason":          getattr(response, "stop_reason", None),
            "model":                _MODEL,
            "usage":                usage_data,
        }

        (day_dir / fname).write_text(
            json.dumps(trace, indent=2, default=str),
            encoding="utf-8",
        )
        logger.debug("trace written: %s/%s", day_dir, fname)
    except Exception as e:    # noqa: BLE001 — never fail real work for tracing
        logger.warning("failed to write qualifier trace: %s", e)


def _parse_json_strict(text: str) -> dict:
    """Extract and parse the JSON object from Claude's output.

    Strategy, cheapest first:
      1. Whole text is already valid JSON (what our prompt asks for).
      2. Otherwise, find the first BALANCED {...} block and parse that.

    Step 2 uses brace-counting rather than a regex so it survives nested
    objects — a regex like `\\{.*?\\}` stops at the first '}' and silently
    corrupts any nested structure. We strip code fences first so a fenced
    block reduces to plain text before brace-scanning.

    Raises ValueError (which json.JSONDecodeError subclasses) on failure.
    """
    if not text or not text.strip():
        raise ValueError("empty text")

    stripped = text.strip()

    # 1. Fast path — the whole thing is JSON.
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # 2. Remove ```json … ``` fences, then scan for a balanced object.
    unfenced = re.sub(r"```(?:json)?|```", "", stripped)
    obj = _first_balanced_object(unfenced)
    if obj is None:
        raise ValueError(f"no balanced JSON object found in: {stripped[:200]!r}")
    return json.loads(obj)


def _first_balanced_object(text: str) -> str | None:
    """Return the first balanced {...} substring, or None.

    Brace-aware and string-aware: braces inside JSON string literals (and
    escaped quotes) don't affect the depth count.
    """
    start = text.find("{")
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
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None
