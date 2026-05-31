"""ICP qualification — rules first, LLM only for the ambiguous middle.

Two-stage gate:
    Stage 1 (free, instant)   — hard rules in passes_rules()
    Stage 2 (~$0.01/call)     — Claude call only if rules pass

This keeps cost predictable. ~80% of incoming signals will be killed by
rules before any LLM money is spent.

Calibration discipline: every LLM verdict with confidence < 0.7 goes to
human review. We do not trust uncalibrated confidence scores blindly.
"""

from __future__ import annotations

import json
import os
import textwrap
from typing import Any

from anthropic import AsyncAnthropic, BadRequestError
from dotenv import load_dotenv

from auto_search.models import QualificationResult, RawSignal

load_dotenv()

_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-5-20251101")
_CONFIDENCE_FLOOR = 0.70


# ─── ICP prompt ──────────────────────────────────────────────────────

ICP_SYSTEM_PROMPT = textwrap.dedent("""
    You are evaluating whether a company that just triggered an intent
    signal fits Magical's Ideal Customer Profile (ICP) for ABM outreach.

    ═══════════════════════════════════════════════════════════════════
    MAGICAL'S ICP
    ═══════════════════════════════════════════════════════════════════

    Magical sells agentic AI for revenue cycle management (RCM) and
    operations automation to US healthcare organizations. Three target
    segments:

    1. SPECIALTY PRACTICES
       - Orthopedics, behavioral health, physical therapy, ambulatory
         surgery centers (ASCs)
       - Multi-location practice groups
       - Size: 100–5,000 employees
       - US-based

    2. PAYERS
       - Medicare Advantage MCOs
       - Medicaid MCOs
       - Regional Blue Cross plans
       - Size: 500+ employees
       - US-based

    3. HEALTH SYSTEMS
       - Community hospitals
       - Mid-market hospital systems
       - Size: 1,000–50,000 employees
       - US-based
       - NOT mega-enterprise (Mayo, Kaiser, HCA, Ascension)

    ═══════════════════════════════════════════════════════════════════
    HARD DISQUALIFIERS
    ═══════════════════════════════════════════════════════════════════

    Mark `qualified: false` if ANY of:
       - Outside the US
       - Pure tech / SaaS company (not a provider or payer)
       - Pharma manufacturer, medical device maker, or biotech
       - Dental-only practice
       - Solo or small clinic (< 100 employees, unless clearly multi-loc)
       - Mega-enterprise health system (Mayo, Kaiser, HCA, Ascension,
         Cleveland Clinic, Providence — these have different motion)
       - Government agency (VA, IHS, etc.)

    ═══════════════════════════════════════════════════════════════════
    YOUR TASK
    ═══════════════════════════════════════════════════════════════════

    Given the signal context below, decide:
      - Does this company fit Magical's ICP?
      - If yes, which segment + sub-segment?
      - How confident are you (0.0–1.0)?

    Be conservative. False positives waste Galyna's review time. When
    unsure, set `needs_human_review: true` rather than guessing.

    ═══════════════════════════════════════════════════════════════════
    OUTPUT FORMAT
    ═══════════════════════════════════════════════════════════════════

    Return ONE JSON object — nothing else, no markdown fences:

    {
      "qualified": true | false,
      "segment": "specialty" | "payer" | "health_system" | null,
      "sub_segment": "ortho" | "behavioral_health" | "pt" | "asc"
                   | "medicare_advantage" | "medicaid_mco" | "bcbs"
                   | "community_hospital" | "mid_market_hs" | null,
      "confidence": 0.0-1.0,
      "reasoning": "1-2 sentence explanation",
      "needs_human_review": true | false
    }

    Rules:
      - If confidence < 0.7, ALWAYS set needs_human_review: true
      - If clearly disqualified, segment + sub_segment = null
      - Keep reasoning short and factual — no marketing language
""").strip()


# ─── Stage 1: rules ──────────────────────────────────────────────────

_HEALTHCARE_INDUSTRY_KEYWORDS = (
    "health", "medical", "hospital", "clinic", "rcm", "revenue cycle",
    "insurance", "payer", "behavioral", "mental", "care", "therapy",
    "orthop", "surgery", "ambulatory",
)

_DISQUALIFYING_INDUSTRY_KEYWORDS = (
    "biotech", "pharma", "medical device", "dental",
    "veterinary", "vet ",
)


def passes_rules(signal: RawSignal) -> tuple[bool, str]:
    """Cheap, deterministic pre-filter. Returns (passed, reason)."""
    payload = signal.payload

    industry = (payload.get("industry_raw") or "").lower()
    if any(k in industry for k in _DISQUALIFYING_INDUSTRY_KEYWORDS):
        return False, f"disqualifying industry: {industry!r}"
    if not any(k in industry for k in _HEALTHCARE_INDUSTRY_KEYWORDS):
        return False, f"non-healthcare industry: {industry!r}"

    country = (payload.get("country") or "").upper().strip()
    if country and country not in {
        "", "USA", "US", "UNITED STATES", "U.S.", "U.S.A."
    }:
        return False, f"non-US: {country!r}"

    laid_off = payload.get("laid_off_count")
    if isinstance(laid_off, int) and laid_off < 10:
        return False, f"too small (laid off {laid_off})"

    return True, "passed rules"


# ─── Stage 2: LLM ────────────────────────────────────────────────────

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
    """Call Claude with the ICP prompt + signal context. Parse JSON out."""
    context = {
        "company": signal.company_name_raw,
        "signal_type": signal.signal_type,
        "observed_at": signal.observed_at.isoformat(),
        "signal_strength_heuristic": signal.signal_strength,
        "payload": signal.payload,
    }

    user_message = (
        "Evaluate the following signal against Magical's ICP.\n\n"
        f"```json\n{json.dumps(context, indent=2, default=str)}\n```\n\n"
        "Respond with ONLY the JSON object."
    )

    resp = await _get_client().messages.create(
        model=_MODEL,
        max_tokens=600,
        system=ICP_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    text = "".join(
        block.text for block in resp.content if block.type == "text"
    ).strip()
    text = _strip_code_fence(text)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return QualificationResult(
            qualified=False,
            confidence=0.0,
            reasoning=f"LLM returned non-JSON: {e}; raw={text[:200]!r}",
            needs_human_review=True,
            decided_by="llm",
        )

    return QualificationResult(**data, decided_by="llm")


# ─── Orchestrator ────────────────────────────────────────────────────

async def qualify(signal: RawSignal) -> QualificationResult:
    """Full two-stage qualification. This is the public entry point."""
    passed, reason = passes_rules(signal)
    if not passed:
        return QualificationResult(
            qualified=False,
            confidence=0.95,
            reasoning=f"Rule-based disqualification: {reason}",
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

    # Calibration discipline: low confidence → human queue
    if result.confidence < _CONFIDENCE_FLOOR:
        result.needs_human_review = True

    return result


# ─── pure helper ─────────────────────────────────────────────────────

def _strip_code_fence(text: str) -> str:
    """Remove ```json ... ``` fences if Claude added them anyway."""
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()
