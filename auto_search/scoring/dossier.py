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
from auto_search.scoring import apollo
from auto_search.scoring.frameworks import scoring_prompt_context
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
# Decision-maker names come from Apollo, so the LLM does NOT web-search for
# people - it spends its (tighter) budget on news, services, and synthesis only.
_MAX_SEARCHES = 7
_MAX_TOKENS = 4500
_CONFIDENCE = ("known", "likely", "unknown")


class DossierError(RuntimeError):
    """Raised when a dossier cannot be produced (the service marks it retryable)."""


async def generate(account: Account, score: ScoreResult) -> tuple[Dossier, float]:
    """Research + write the dossier for one account. Returns (dossier, cost_usd).
    Raises DossierError on an unrecoverable failure."""
    # Decision makers come from Apollo (deterministic names, no LLM people-search
    # and no name hallucination). The model only adds the relevance notes.
    people = await apollo.decision_makers(account.domain)
    system = _system_prompt(bool(people))
    user = _user_message(account, score, people)

    logger.info("generating dossier for %r (%d Apollo contacts)", account.name, len(people))
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
    # Names + titles are Apollo's; the model supplies the parallel relevance notes.
    dossier.decision_makers = _merge_people(people, data.get("decision_maker_notes"))
    dossier.model = _MODEL
    dossier.generated_at = datetime.now(UTC).isoformat()
    dossier.cost_usd = cost
    logger.info("  -> dossier for %s (%d searches, $%.3f, %d decision makers)",
                account.name, len(queries), cost, len(dossier.decision_makers))
    return dossier, cost


def _merge_people(people: list[dict], notes) -> list[DecisionMaker]:
    """Apollo names/titles + the model's per-person relevance notes (by order)."""
    note_list = _strs(notes)
    out: list[DecisionMaker] = []
    for i, p in enumerate(people):
        out.append(DecisionMaker(
            role=p.get("title", ""),
            contact=p.get("name", ""),
            notes=note_list[i] if i < len(note_list) else "",
            linkedin=p.get("linkedin", ""),
        ))
    return out


# ── prompt building ───────────────────────────────────────────────────


def _system_prompt(has_people: bool) -> str:
    people = (
        "You are also given a list of CONFIRMED decision makers (name + title, "
        "from CRM data) in priority order. Do NOT search for individual people. "
        'For each one, in the SAME ORDER, write a single sentence on why they '
        'matter to an RCM automation sale, returned as "decision_maker_notes".'
        if has_people else
        'No decision makers were available from CRM data, so return an empty '
        '"decision_maker_notes" array and do NOT search for individual people.'
    )
    # Plain string (not an f-string) so the JSON braces stay literal; the people
    # clause is dropped in via a token.
    body = textwrap.dedent("""
        You are a senior account-based-marketing analyst for Magical, an
        agentic-AI revenue cycle management (RCM) platform sold to US healthcare
        organizations. Write a sales dossier (a one-pager) the sales team takes
        into an account.

        The account has already been scored. You are given its fit score, the
        per-pillar dimension summaries, and its known facts - treat those as
        AUTHORITATIVE and do not re-research them. __PEOPLE_CLAUSE__

        __DATE_CONTEXT__

        Spend web_search only on what is not already provided:
          - recent, dated news (EHR go-lives, M&A, leadership moves, funding,
            awards, expansions);
          - specific service lines / specialties and payer mix;
          - RCM complexity drivers (multi-site, multi-service-line, VBC/CIN,
            M&A integration).
        Be economical with searches - reuse what one search returns across
        sections rather than searching again. Never collect emails or phone
        numbers.

        Then synthesize the entry strategy, pain points, and messaging angles
        from everything above. Messaging angles are ready-to-send outreach lines
        that reference the account's real, current situation.

        Be specific and evidence-rich: cite named EHR/RCM vendors, revenue
        figures, headcounts, location counts, and dates. Mark every fact's
        confidence honestly: "known" (publicly confirmed), "likely" (reasonably
        inferred), or "unknown" (could not confirm). Never invent a number; if a
        fact is unconfirmed, mark it "unknown".

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
          "decision_maker_notes": [
            "<one sentence on decision maker 1 (same order as provided)>",
            "<one sentence on decision maker 2>"
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

        Aim for 6-10 firmographic rows, 3-5 intent signals, 3-5 recent news
        items, 4-6 pain points, and 3-5 messaging angles.
    """)
    return (body.replace("__PEOPLE_CLAUSE__", people)
            .replace("__DATE_CONTEXT__", scoring_prompt_context())
            .strip())


def _user_message(account: Account, score: ScoreResult, people: list[dict]) -> str:
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
        "confirmed_decision_makers": [
            {"name": p.get("name"), "title": p.get("title")} for p in people
        ],
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
    # decision_makers are set by generate() from Apollo + the parallel notes.
    return Dossier(
        firmographic_profile=_facts(data.get("firmographic_profile")),
        services=_facts(data.get("services")),
        intent_signals=_signals(data.get("intent_signals")),
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
