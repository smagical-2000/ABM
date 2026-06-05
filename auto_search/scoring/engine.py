"""The scorer — Claude evaluates an account against its segment rubric.

Generic over the framework config: the rubric (dimensions, ceilings, guidance)
is rendered into the prompt, so this engine never changes when a rubric does.
Known facts (CSV columns, discovery firmographics) are injected as authoritative
so the model researches only what it doesn't already have — cheaper and more
accurate. web_search supplies the rest (competitor, pain, intent).

Returns a ScoreResult WITHOUT the QA verdict; QA is an independent pass
(qa.py) so it can't be anchored by the scorer's reasoning.
"""

from __future__ import annotations

import json
import logging
import os
import textwrap
from datetime import UTC, datetime

from auto_search import llm
from auto_search.scoring.frameworks import (
    Framework,
    framework_for_segment,
    resolve_tier,
    scoring_prompt_context,
)
from auto_search.scoring.models import Account, Dimension, ScoreResult

logger = logging.getLogger(__name__)

_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
# web_search is the dominant cost (each result is re-sent on later turns, so
# searches compound the input tokens). Once known facts are injected the scorer
# spends searches on competitor/pain/intent, not on facts it already has:
#   - discovery accounts arrive with the qualification research (firmographics,
#     reasoning, evidence) already done, so the scorer only needs competitor /
#     RCM vendor / pain / intent.
#   - CSV imports arrive with firmographics + technographics from Definitive, so
#     they need even fewer (intent only).
_MAX_SEARCHES = 3
_MAX_SEARCHES_CSV = 2
_MAX_TOKENS = 2000


def _max_searches(account: Account) -> int:
    return _MAX_SEARCHES_CSV if account.source == "csv" else _MAX_SEARCHES


class ScoringError(RuntimeError):
    """Raised when a score cannot be produced (the service marks it retryable)."""


async def score_account(account: Account, prior: ScoreResult | None = None) -> ScoreResult:
    """Score one account. Raises ScoringError on an unrecoverable failure.

    On a re-score, `prior` carries the account's last official scores so the
    model holds them steady unless web_search finds new dated evidence - keeping
    re-scores consistent on top of temperature 0.
    """
    fw = _framework(account)
    system = _system_prompt(fw)
    user = _user_message(account, fw, prior)

    logger.info("scoring %r on %s rubric", account.name, fw.key)
    try:
        response = await llm.call_with_web_search(
            system=system, user_message=user,
            max_searches=_max_searches(account), max_tokens=_MAX_TOKENS, model=_MODEL,
            temperature=0,                       # deterministic: re-scores stay consistent
        )
    except Exception as e:  # noqa: BLE001 — surface as a scoring failure
        raise ScoringError(f"LLM call failed: {type(e).__name__}: {e}") from e

    text = llm.extract_text(response)
    try:
        data = llm.parse_json_object(text)
    except ValueError as e:
        raise ScoringError(f"scorer returned non-JSON: {e}") from e

    dims = _parse_dimensions(fw, data.get("dimensions", []))
    result = ScoreResult(
        account_id=account.account_id,
        framework=fw.key,
        framework_version=fw.version,
        dimensions=dims,
        total=0,
        max_total=fw.max_total,
        tier_band="low",
        tier_label="",
        recommendation=str(data.get("recommendation", "")).strip(),
        model=_MODEL,
        scored_at=datetime.now(UTC).isoformat(),
    ).clamp()

    band = resolve_tier(fw, result.total, [d.model_dump() for d in dims])
    result.tier_band, result.tier_label = band.band, band.label

    queries = llm.extract_web_searches(response)
    result.cost_usd = llm.call_cost(response, searches=len(queries))
    logger.info("  -> %s %d/%d  (%d searches, $%.3f)",
                result.tier_label, result.total, result.max_total,
                len(queries), result.cost_usd)
    if queries:
        logger.info("  scorer searches for %s: %s", account.name, " | ".join(queries))
    return result


# ── prompt building ───────────────────────────────────────────────────


def _framework(account: Account) -> Framework:
    # Trust the resolved framework on the account; fall back to the segment map.
    from auto_search.scoring.frameworks import FRAMEWORKS
    return FRAMEWORKS.get(account.framework) or framework_for_segment(account.segment)


def _system_prompt(fw: Framework) -> str:
    dims = "\n".join(
        f"  - {d.key} | {d.label} (0-{d.max}): {d.guidance}"
        for d in fw.dimensions
    )
    return textwrap.dedent(f"""
        You are an ABM analyst for Magical, an agentic-AI revenue cycle
        management platform sold to US healthcare organizations.

        {fw.intro}

        {scoring_prompt_context()}

        Use the web_search tool sparingly to research the company. You have a
        small search budget, so spend it only on what you do NOT already have:
        competitor/automation vendors, pain signals, leadership changes, and
        recent intent. When a fact is given to you as a KNOWN FACT (from a CSV
        import), treat it as authoritative and do NOT search for it — those
        CSV values are confirmed data, not "inferred".

        FLAGS on each dimension (not on individual CSV fields):
        - No flag: at least one named, dated public fact supports the score.
        - "inferred": partial evidence only — a reasonable estimate for what
          is NOT in KNOWN FACTS (e.g. RCM vendor, buying intent). Do NOT flag a
          dimension "inferred" when KNOWN FACTS already supply the numbers for
          that dimension (physicians, locations, Epic, Medicare allowed, etc.).
          Do NOT use "inferred" on intent scores above 6/10.
        - "unknown": no evidence and no reliable pattern — score that dimension
          low (typically 4/10 or below).

        Score every dimension below. The score must not exceed the dimension's
        ceiling.

        DIMENSIONS (key | label (0-max): guidance):
        {dims}

        Be specific and evidence-rich in every summary: cite revenue figures,
        named EHR / RCM / automation vendors (e.g. Epic, MEDITECH, Notable,
        AssortHealth, ThoughtfulAI, UiPath), headcounts and location counts,
        named leaders and titles, and dates where you find them. Vague summaries
        are not acceptable.

        Then write a 2-3 sentence recommendation: the fit, the wedge, and the
        play.

        Return ONLY this JSON object, no prose, no markdown fences:
        {{
          "dimensions": [
            {{ "key": "<dimension key>", "score": <number 0..max>,
               "summary": "<1-2 specific sentences citing concrete evidence>",
               "flags": ["inferred" | "unknown"]  // omit or [] when confident
            }}
          ],
          "recommendation": "<2-3 sentences>"
        }}

        Include every dimension key exactly once. No extra keys.
    """).strip()


def _user_message(account: Account, fw: Framework, prior: ScoreResult | None = None) -> str:
    known = _known_facts_block(account)
    signals = _signals_block(account)
    prior_block = _prior_block(prior)
    ctx = {
        "company": account.name,
        "segment": account.segment,
        "sub_segment": account.sub_segment,
        "domain": account.domain,
        "approximate_employees": account.approximate_employees,
    }
    return textwrap.dedent(f"""
        Score this account on the {fw.label} rubric.

        Account:
        ```json
        {json.dumps(ctx, indent=2, default=str)}
        ```
        {known}{signals}{prior_block}
        Respond with ONLY the JSON object described in the system prompt.
    """).strip()


def _prior_block(prior: ScoreResult | None) -> str:
    if prior is None or not prior.dimensions:
        return ""
    dims = [
        {"key": d.key, "label": d.label, "score": d.score, "max": d.max}
        for d in prior.dimensions
    ]
    facts = json.dumps(dims, indent=2, default=str)
    return (
        "\nPRIOR OFFICIAL SCORES (this account was scored before):\n"
        f"```json\n{facts}\n```\n"
        "Change a dimension's score only if web_search finds NEW dated evidence "
        "since the last score; otherwise return the same scores and summaries. "
        "Do not re-research the KNOWN FACTS.\n"
    )


def _known_facts_block(account: Account) -> str:
    if not account.firmographics:
        return ""
    facts = json.dumps(account.firmographics, indent=2, default=str)
    return (
        "\nKNOWN FACTS (authoritative — do not re-research these):\n"
        f"```json\n{facts}\n```\n"
    )


def _signals_block(account: Account) -> str:
    if not account.discovery_signals:
        return ""
    lines = [
        f"  - {s.get('signal_type', 'signal')}: {s.get('summary', '')}".rstrip()
        for s in account.discovery_signals
    ]
    return ("\nDISCOVERY SIGNALS (carried intent — weight toward the intent "
            "dimension):\n" + "\n".join(lines) + "\n")


# ── parsing ───────────────────────────────────────────────────────────


def _parse_dimensions(fw: Framework, raw: list) -> list[Dimension]:
    """Build exactly the framework's dimensions, in order, from model output.

    Missing dimensions default to 0 and an "unknown" flag, so the result shape
    is always complete and the total can't be silently short.
    """
    by_key = {}
    for item in raw or []:
        if isinstance(item, dict) and item.get("key"):
            by_key[str(item["key"])] = item

    dims: list[Dimension] = []
    for spec in fw.dimensions:
        item = by_key.get(spec.key, {})
        flags = item.get("flags") or []
        if not isinstance(flags, list):
            flags = []
        present = spec.key in by_key
        dims.append(Dimension(
            key=spec.key,
            label=spec.label,
            max=spec.max,
            score=item.get("score", 0),
            summary=str(item.get("summary", "")).strip(),
            flags=[str(f) for f in flags] if present else ["unknown"],
        ))
    return dims
