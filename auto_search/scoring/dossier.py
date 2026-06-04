"""Landing-page dossier generator.

A deep-research sales one-pager for an account being pursued: firmographic
profile, services, scored intent signals, decision makers, entry strategy, RCM
complexity, recent news, pain points, and messaging angles. This is the richest
(and most expensive) LLM pass in the system, so it runs only on demand.

It is layered on the score, not independent of it: the fit, pillars, dimension
summaries, and known facts are handed in as authoritative context, so the search
budget is spent on what the score did NOT establish - named decision makers,
dated news, service lines, and the synthesis (entry strategy / messaging). The
fit-score section of the rendered page comes straight from the score, so this
model never re-derives it.
"""

from __future__ import annotations

import json
import logging
import os
import textwrap
from datetime import UTC, datetime

from auto_search import llm
from auto_search.scoring.models import (
    Account,
    DecisionMaker,
    Dossier,
    EntryStrategy,
    FactRow,
    IntentSignal,
    NewsItem,
    ScoreResult,
)

logger = logging.getLogger(__name__)

_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
# Deep research: this pass legitimately needs many searches (people, news,
# services). It is on demand + one-time per account, with the cost surfaced
# before it runs, so the budget is wider than scoring.
_MAX_SEARCHES = 12
_MAX_TOKENS = 4500
_CONFIDENCE = ("known", "likely", "unknown")


class DossierError(RuntimeError):
    """Raised when a dossier cannot be produced (the service marks it retryable)."""


async def generate(account: Account, score: ScoreResult) -> tuple[Dossier, float]:
    """Research + write the dossier for one account. Returns (dossier, cost_usd).
    Raises DossierError on an unrecoverable failure."""
    system = _system_prompt()
    user = _user_message(account, score)

    logger.info("generating dossier for %r", account.name)
    try:
        response = await llm.call_with_web_search(
            system=system, user_message=user,
            max_searches=_MAX_SEARCHES, max_tokens=_MAX_TOKENS, model=_MODEL,
        )
    except Exception as e:  # noqa: BLE001 — surface as a dossier failure
        raise DossierError(f"LLM call failed: {type(e).__name__}: {e}") from e

    try:
        data = llm.parse_json_object(llm.extract_text(response))
    except ValueError as e:
        raise DossierError(f"dossier returned non-JSON: {e}") from e

    queries = llm.extract_web_searches(response)
    cost = llm.call_cost(response, searches=len(queries))
    dossier = _parse(data)
    dossier.model = _MODEL
    dossier.generated_at = datetime.now(UTC).isoformat()
    dossier.cost_usd = cost
    logger.info("  -> dossier for %s (%d searches, $%.3f, %d decision makers)",
                account.name, len(queries), cost, len(dossier.decision_makers))
    return dossier, cost


# ── prompt building ───────────────────────────────────────────────────


def _system_prompt() -> str:
    return textwrap.dedent("""
        You are a senior account-based-marketing analyst for Magical, an
        agentic-AI revenue cycle management (RCM) platform sold to US healthcare
        organizations. Write a sales dossier (a one-pager) the sales team takes
        into an account.

        The account has already been scored. You are given its fit score, the
        per-pillar dimension summaries, and its known facts - treat those as
        AUTHORITATIVE and do not re-research them. Spend web_search only on what
        the score did not establish:
          - named decision makers (CEO, CFO, VP/Director of Revenue Cycle, CIO,
            General Counsel) with titles and, where public, how to reach them;
          - recent, dated news (EHR go-lives, M&A, leadership moves, funding,
            awards, expansions);
          - specific service lines / specialties and payer mix;
          - RCM complexity drivers (multi-site, multi-service-line, VBC/CIN,
            M&A integration).

        Then synthesize the entry strategy, pain points, and messaging angles
        from everything above. Messaging angles are ready-to-send outreach lines
        that reference the account's real, current situation.

        Be specific and evidence-rich: cite named EHR/RCM vendors, revenue
        figures, headcounts, location counts, named people + titles, and dates.
        Mark every fact's confidence honestly: "known" (publicly confirmed),
        "likely" (reasonably inferred), or "unknown" (could not confirm). Never
        invent a person, contact, or number; if a decision-maker seat is open or
        a fact is unconfirmed, say so and mark it "unknown".

        Return ONLY this JSON object, no prose, no markdown fences:
        {
          "firmographic_profile": [
            { "label": "Category|Headquarters|Founded|Locations|Employees|Revenue|Ownership|Market Position|Key Differentiator",
              "value": "<specific value>", "confidence": "known|likely|unknown" }
          ],
          "services": [
            { "label": "Core|Specialties|Additional|Payer Mix",
              "value": "<specific value>", "confidence": "known|likely|unknown" }
          ],
          "intent_signals": [
            { "signal": "<headline>", "detail": "<1-2 sentences with evidence>",
              "score": <0-10 buying-signal strength> }
          ],
          "decision_makers": [
            { "role": "<title/function>", "contact": "<name + title, or 'Unknown (seat open)'>",
              "notes": "<why they matter to an RCM sale>" }
          ],
          "entry_strategy": {
            "timing": "HIGH|MEDIUM|LOW - <one line why now>",
            "primary_angles": ["<angle 1>", "<angle 2>", "..."],
            "cautions": ["<what could land poorly>", "..."],
            "deal_size": "<$ range with the basis>"
          },
          "rcm_complexity": [
            { "label": "Payer Mix|Multi-Site|Multi-Service Line|EHR System|VBC / CIN Contracts|M&A Integration",
              "value": "<the complexity driver>", "confidence": "known|likely|unknown" }
          ],
          "recent_news": [
            { "headline": "<event>", "detail": "<what + why it matters>", "date": "<when, if known>" }
          ],
          "pain_points": ["<RCM pain tied to their situation>", "..."],
          "messaging_angles": ["<ready-to-send outreach line>", "..."]
        }

        Aim for 6-10 firmographic rows, 3-5 intent signals, 3-6 decision makers,
        3-5 recent news items, 4-6 pain points, and 3-5 messaging angles.
    """).strip()


def _user_message(account: Account, score: ScoreResult) -> str:
    dims = [
        {"dimension": d.label, "score": f"{d.score}/{d.max}", "summary": d.summary}
        for d in score.dimensions
    ]
    signals = [
        {"type": s.get("signal_type"), "summary": s.get("summary")}
        for s in (account.discovery_signals or [])
    ]
    ctx = {
        "company": account.name,
        "domain": account.domain,
        "segment": account.segment,
        "sub_segment": account.sub_segment,
        "fit": f"{score.total}/{score.max_total} ({score.tier_label})",
        "known_facts": account.firmographics or {},
        "score_dimensions": dims,
        "recommendation": score.recommendation,
        "discovery_signals": signals,
    }
    return textwrap.dedent(f"""
        Write the dossier for this account. The score below is authoritative
        context - build on it, research the gaps, and do not re-derive it.

        ```json
        {json.dumps(ctx, indent=2, default=str)}
        ```
        Respond with ONLY the JSON object described in the system prompt.
    """).strip()


# ── parsing ───────────────────────────────────────────────────────────


def _parse(data: dict) -> Dossier:
    return Dossier(
        firmographic_profile=_facts(data.get("firmographic_profile")),
        services=_facts(data.get("services")),
        intent_signals=_signals(data.get("intent_signals")),
        decision_makers=_people(data.get("decision_makers")),
        entry_strategy=_entry(data.get("entry_strategy")),
        rcm_complexity=_facts(data.get("rcm_complexity")),
        recent_news=_news(data.get("recent_news")),
        pain_points=_strs(data.get("pain_points")),
        messaging_angles=_strs(data.get("messaging_angles")),
    )


def _facts(raw) -> list[FactRow]:
    out: list[FactRow] = []
    for it in raw or []:
        if not isinstance(it, dict) or not it.get("label"):
            continue
        conf = str(it.get("confidence", "known")).lower()
        out.append(FactRow(
            label=str(it["label"]).strip(),
            value=str(it.get("value", "")).strip(),
            confidence=conf if conf in _CONFIDENCE else "known",
        ))
    return out


def _signals(raw) -> list[IntentSignal]:
    out: list[IntentSignal] = []
    for it in raw or []:
        if not isinstance(it, dict) or not it.get("signal"):
            continue
        out.append(IntentSignal(
            signal=str(it["signal"]).strip(),
            detail=str(it.get("detail", "")).strip(),
            score=_clamp_int(it.get("score"), 0, 10),
        ))
    return out


def _people(raw) -> list[DecisionMaker]:
    out: list[DecisionMaker] = []
    for it in raw or []:
        if not isinstance(it, dict) or not it.get("role"):
            continue
        out.append(DecisionMaker(
            role=str(it["role"]).strip(),
            contact=str(it.get("contact", "")).strip(),
            notes=str(it.get("notes", "")).strip(),
        ))
    return out


def _news(raw) -> list[NewsItem]:
    out: list[NewsItem] = []
    for it in raw or []:
        if not isinstance(it, dict) or not it.get("headline"):
            continue
        out.append(NewsItem(
            headline=str(it["headline"]).strip(),
            detail=str(it.get("detail", "")).strip(),
            date=str(it.get("date", "")).strip(),
        ))
    return out


def _entry(raw) -> EntryStrategy:
    if not isinstance(raw, dict):
        return EntryStrategy()
    return EntryStrategy(
        timing=str(raw.get("timing", "")).strip(),
        primary_angles=_strs(raw.get("primary_angles")),
        cautions=_strs(raw.get("cautions")),
        deal_size=str(raw.get("deal_size", "")).strip(),
    )


def _strs(raw) -> list[str]:
    return [str(x).strip() for x in (raw or []) if str(x).strip()]


def _clamp_int(v, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(round(float(v)))))
    except (TypeError, ValueError):
        return 0
