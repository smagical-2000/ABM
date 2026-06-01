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

import json
import logging
import os
import textwrap
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from anthropic import BadRequestError
from dotenv import load_dotenv

from auto_search import llm
from auto_search.models import MIN_LAID_OFF, QualificationResult, RawSignal
from auto_search.normalize import slugify

load_dotenv(override=True)
logger = logging.getLogger(__name__)

_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
_WEB_SEARCH_MAX_USES = 6        # cap searches per evaluation (cost guardrail)
_MAX_TOKENS          = 1200
_CONFIDENCE_FLOOR    = 0.70

# Per-call trace dir — every qualification writes a JSON file here so you
# can read exactly what Claude saw, searched for, and concluded. Critical
# for debugging prompt issues without re-running expensive LLM calls.
_TRACE_DIR = Path(os.getenv("QUALIFIER_TRACE_DIR", "./data/qualifier_traces"))

# Country values treated as US for the structural pre-filter. Empty is
# allowed because WARN data carries no country field (US-only by statute).
_US_COUNTRY_VALUES = {"", "usa", "us", "united states", "u.s.", "u.s.a."}


# ═════════════════════════════════════════════════════════════════════
# ICP PROMPT
# ═════════════════════════════════════════════════════════════════════

ICP_SYSTEM_PROMPT = textwrap.dedent("""
    You are an account researcher for Magical, an agentic-AI revenue
    cycle management (RCM) platform sold to US healthcare organizations.

    A buying signal has surfaced this company — it may be a layoff, a
    leadership change (new CFO/CMO/etc.), an acquisition, or another event.
    The signal type and details are in the user message. Your job: determine
    whether the company fits Magical's Ideal Customer Profile (ICP). Use the
    web_search tool to research the company's actual website and operations —
    judge the COMPANY, not the signal; do NOT guess from the name alone.

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
      "domain": "<company's primary domain, e.g. orthoindy.com, or null>",
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
# Stage 2 — Claude with web_search (shared helpers live in llm.py)
# ═════════════════════════════════════════════════════════════════════


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

    response = await llm.call_with_web_search(
        system=ICP_SYSTEM_PROMPT,
        user_message=user_message,
        max_searches=_WEB_SEARCH_MAX_USES,
        max_tokens=_MAX_TOKENS,
        model=_MODEL,
    )

    text = llm.extract_text(response)
    web_searches = llm.extract_web_searches(response)
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
        data = llm.parse_json_object(text)
    except ValueError as e:
        parse_error = str(e)
        logger.warning("qualifier got non-JSON for %s: %s",
                       signal.company_name_raw, e)
        verdict = QualificationResult(
            qualified=False,
            confidence=0.0,
            reasoning=f"LLM returned non-JSON output: {e}",
            needs_human_review=True,
            is_error=True,
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
                is_error=True,
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
            is_error=True,
            decided_by="rules+llm",
        )
    except Exception as e:    # noqa: BLE001 — never crash the pipeline
        logger.error("LLM call failed for %s: %s", signal.company_name_raw, e)
        return QualificationResult(
            qualified=False,
            confidence=0.0,
            reasoning=f"LLM call failed: {type(e).__name__}: {e}",
            needs_human_review=True,
            is_error=True,
            decided_by="rules+llm",
        )

    # Confidence floor — LLM scores are not calibrated; trust but verify
    if result.confidence < _CONFIDENCE_FLOOR:
        result.needs_human_review = True

    result.decided_by = "rules+llm"
    return result


# ═════════════════════════════════════════════════════════════════════
# Trace persistence
# ═════════════════════════════════════════════════════════════════════

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
        now = datetime.now(UTC)
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
