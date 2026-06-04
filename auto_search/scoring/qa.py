"""Independent QA — a second Claude pass that verifies a score.

Trust but verify. QA receives the account, its known facts, and the per-
dimension scores the first analyst assigned (numbers only — never the scorer's
summaries or reasoning), then independently researches the key verifiable facts
(NPR, EMR/RCM vendor, lives covered, size, recent signals) and flags
disagreements.

Whether a disagreement is "tier-changing" is computed deterministically here,
not left to the model: we apply QA's corrected scores and re-resolve the tier.
A tier-changing discrepancy is the loud one the dashboard surfaces.
"""

from __future__ import annotations

import json
import logging
import os
import textwrap

from auto_search import llm
from auto_search.scoring.frameworks import Framework, resolve_tier
from auto_search.scoring.models import Account, QACorrection, QAResult, ScoreResult

logger = logging.getLogger(__name__)

_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
_MAX_SEARCHES = 3
_MAX_TOKENS = 1400


async def qa_account(
    account: Account, score: ScoreResult, fw: Framework
) -> tuple[QAResult, float]:
    """Independently verify a score. Never raises — QA failure yields an
    'unverifiable' verdict so a score still ships (the human is the backstop).

    Returns (verdict, cost_usd) so the service can add the QA call to the
    account's measured spend.
    """
    system = _qa_system_prompt(fw)
    user = _qa_user_message(account, score)

    try:
        response = await llm.call_with_web_search(
            system=system, user_message=user,
            max_searches=_MAX_SEARCHES, max_tokens=_MAX_TOKENS, model=_MODEL,
        )
        data = llm.parse_json_object(llm.extract_text(response))
    except Exception as e:  # noqa: BLE001 — QA must not fail the score
        logger.warning("QA pass failed for %s: %s", account.name, e)
        return QAResult(status="unverifiable",
                        notes="Independent QA could not complete.", corrections=[]), 0.0

    cost = llm.call_cost(response, searches=len(llm.extract_web_searches(response)))
    corrections = _parse_corrections(data.get("corrections", []))
    status = data.get("status")
    if status not in ("verified", "discrepancy", "unverifiable"):
        status = "discrepancy" if corrections else "verified"

    qa = QAResult(
        status=status,
        notes=str(data.get("notes", "")).strip(),
        corrections=corrections,
    )
    mark_tier_changing(fw, score, qa)
    return qa, cost


def mark_tier_changing(fw: Framework, score: ScoreResult, qa: QAResult) -> None:
    """Set qa.tier_changing deterministically: apply QA's corrected scores and
    check whether the tier band moves."""
    if not qa.corrections:
        qa.tier_changing = False
        return

    by_key = {d.key: d for d in score.dimensions}
    by_label = {d.label.lower(): d for d in score.dimensions}
    corrected = {d.key: float(d.score) for d in score.dimensions}

    changed = False
    for c in qa.corrections:
        if c.corrected_score is None:
            continue
        dim = by_key.get(c.dimension) or by_label.get((c.dimension or "").lower())
        if dim is not None:
            corrected[dim.key] = max(0.0, min(float(c.corrected_score), float(dim.max)))
            changed = True
    if not changed:
        qa.tier_changing = False
        return

    new_total = int(round(sum(corrected.values())))
    new_dims = [{"key": k, "score": v} for k, v in corrected.items()]
    new_band = resolve_tier(fw, new_total, new_dims)
    qa.tier_changing = new_band.band != score.tier_band


# ── prompt building ───────────────────────────────────────────────────


def _qa_system_prompt(fw: Framework) -> str:
    ceilings = ", ".join(f"{d.label} (0-{d.max})" for d in fw.dimensions)
    return textwrap.dedent(f"""
        You are an independent QA reviewer for Magical's ABM scoring. A first
        analyst has scored an account on the {fw.label} rubric. You are given the
        account, its known facts, and the per-dimension scores the analyst
        assigned — but NOT their reasoning. Do not assume the analyst is right.

        Independently use web_search to verify the materially checkable facts
        (e.g. net patient revenue, EMR/RCM vendor, lives covered, organization
        size, recent leadership or signal claims). Dimensions: {ceilings}.

        For any dimension where your finding would materially change the score,
        add a correction: what the analyst's score implies, what you found, and
        the score you believe is correct.

        Decide a status:
          - "verified": the checkable facts hold up.
          - "discrepancy": one or more material disagreements (add corrections).
          - "unverifiable": key facts cannot be confirmed from public sources.

        Return ONLY this JSON object, no prose, no fences:
        {{
          "status": "verified" | "discrepancy" | "unverifiable",
          "notes": "<1-2 sentences>",
          "corrections": [
            {{ "dimension": "<dimension key>",
               "claimed": "<what the analyst's score implies>",
               "found": "<what you found>",
               "corrected_score": <number or null> }}
          ]
        }}
    """).strip()


def _qa_user_message(account: Account, score: ScoreResult) -> str:
    claimed = [
        {"dimension": d.key, "label": d.label, "score": d.score, "max": d.max}
        for d in score.dimensions
    ]
    ctx = {
        "company": account.name,
        "segment": account.segment,
        "domain": account.domain,
        "known_facts": account.firmographics or {},
        "assigned_scores": claimed,
        "assigned_total": score.total,
        "max_total": score.max_total,
    }
    return textwrap.dedent(f"""
        Independently QA this score.

        ```json
        {json.dumps(ctx, indent=2, default=str)}
        ```
        Respond with ONLY the JSON object described in the system prompt.
    """).strip()


def _parse_corrections(raw: list) -> list[QACorrection]:
    out: list[QACorrection] = []
    for item in raw or []:
        if not isinstance(item, dict) or not item.get("dimension"):
            continue
        try:
            out.append(QACorrection(
                dimension=str(item["dimension"]),
                claimed=str(item.get("claimed", "")),
                found=str(item.get("found", "")),
                corrected_score=_num(item.get("corrected_score")),
            ))
        except Exception:  # noqa: BLE001 — skip a malformed correction
            continue
    return out


def _num(v: object) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None
