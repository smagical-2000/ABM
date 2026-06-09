"""Cheap batched Sonnet pass: tag each headline with a topic + 'why it matters'.

No web_search, no per-article call — one batched call over the day's NEW
headlines (titles only), so it's pennies a day. Fails soft: on any error the
batch keeps its feed-assigned topic and stays relevant, so a model hiccup never
blanks the feed.
"""

from __future__ import annotations

import json
import logging
import os
import textwrap

from auto_search import llm
from auto_search.news.models import TOPICS, NewsItem

logger = logging.getLogger(__name__)

_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
_BATCH = 40
_MAX_TOKENS = 3000

SYSTEM_PROMPT = textwrap.dedent("""
    You triage news for Magical, an agentic-AI revenue cycle management (RCM)
    platform sold to US healthcare providers and payers. For each article
    (title + source) decide:

    - relevant: true ONLY if it bears on US healthcare REVENUE CYCLE / back-office
      reimbursement: billing, claims, prior authorization, denials/appeals,
      eligibility/benefits verification, payer or CMS policy that affects
      reimbursement, or healthcare-AI that automates that work. Set false for
      generic clinical/health news, pharma, devices, plain hospital M&A,
      unrelated AI.
    - topic: one of prior_auth | denials | rcm_ai | eligibility | policy |
      operations (best single fit; "policy" for CMS/regulatory, "rcm_ai" when
      automation/AI is the angle, "operations" for general RCM ops).
    - why_it_matters: ONE sentence on the angle for a seller of RCM automation
      (the wedge / urgency it creates). "" when not relevant.

    Return ONLY a JSON array, one object per id, every id echoed exactly:
    [{"id": "<id>", "relevant": true|false,
      "topic": "prior_auth"|..., "why_it_matters": "<one sentence>"}]
    No prose, no markdown fences.
""").strip()


async def enrich(items: list[NewsItem]) -> float:
    """Classify + annotate items IN PLACE. Returns total measured cost (USD)."""
    total = 0.0
    for start in range(0, len(items), _BATCH):
        batch = items[start:start + _BATCH]
        rows = [{"id": str(i), "title": it.title, "source": it.source or ""}
                for i, it in enumerate(batch)]
        user = ("Triage these articles. Return the JSON array described in the "
                "system prompt.\n\n```json\n" + json.dumps(rows, ensure_ascii=False) + "\n```")
        try:
            response = await llm.call_plain(system=SYSTEM_PROMPT, user_message=user,
                                            max_tokens=_MAX_TOKENS, model=_MODEL)
            verdicts = {
                str(r.get("id")): r
                for r in llm.parse_json_array(llm.extract_text(response))
                if isinstance(r, dict)
            }
            spend = llm.spend_from_response(response, model=_MODEL)
            if spend:
                total += spend.cost_usd
        except Exception as e:  # noqa: BLE001 — a bad batch must not blank the feed
            logger.warning("news enrich batch failed (%s) — keeping %d as-is", e, len(batch))
            continue
        for i, it in enumerate(batch):
            v = verdicts.get(str(i))
            if not v:
                continue
            it.relevant = bool(v.get("relevant", True))
            topic = (v.get("topic") or "").strip()
            if topic in TOPICS:
                it.topic = topic
            it.why_it_matters = (v.get("why_it_matters") or "").strip() or None
    return round(total, 4)
