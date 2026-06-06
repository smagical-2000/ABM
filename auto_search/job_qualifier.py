"""Job-level qualification — is THIS posting the exact RCM role we want?

The connectors find postings by quoted title search, but a title alone is
noisy: "Coding Instructor", "RCM Software Engineer", "Billing Sales Rep" and
"Patient Access Director (strategy)" all match a keyword yet are NOT the
hands-on revenue-cycle work Magical automates. This module reads each posting's
title + description and keeps only genuine operational RCM roles.

It is the CHEAP middle layer of the funnel:

    role keyword gate (free)  →  JOB qualifier (this, Sonnet, no web)  →
    COMPANY/ICP qualifier (expensive, web_search)

Filtering here means we don't spend a web_search company qualification on a
company whose only "RCM" posting was a software-engineer or trainer role.

Cost: one Sonnet call per batch of postings (no tools), title + clipped JD.
Batched to keep it a few cents per run. Fails OPEN — if the model errors or a
verdict is missing, the posting is kept (the company qualifier is the backstop).
"""

from __future__ import annotations

import json
import logging
import os
import textwrap

from pydantic import BaseModel, Field

from auto_search import llm
from auto_search.models import RawSignal

logger = logging.getLogger(__name__)

_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
_BATCH_SIZE = 8
_JD_CHARS = 700          # JD chars sent per posting (token control)
_MAX_TOKENS = 1500


SYSTEM_PROMPT = textwrap.dedent("""
    You filter job postings for Magical, an agentic-AI revenue cycle
    management (RCM) platform sold to US healthcare providers and payers.

    Magical AUTOMATES hands-on revenue-cycle operations work. A posting is
    RELEVANT only if it is an operational RCM role doing (or directly
    supervising) work like:
      • medical billing / claim submission
      • medical coding (CPC/CCS, inpatient/outpatient/profee)
      • accounts receivable / collections / patient accounts follow-up
      • denials / appeals management
      • prior authorization / pre-certification
      • insurance eligibility / benefits verification
      • charge capture / charge entry / payment posting
      • patient access / registration / financial clearance

    Mark RELEVANT = false when the posting is NOT that hands-on RCM work,
    even if the title keyword-matched. Examples to REJECT:
      • Educator / instructor / trainer / faculty / curriculum
      • Software / engineering / data / product roles (incl. building RCM
        software) — these are vendor product roles, not RCM operations
      • Sales / account executive / marketing / business development
      • Pure clinical roles (nurse, physician, MA) with no RCM duties
      • Corporate finance/accounting unrelated to patient revenue
        (AP, payroll, treasury, FP&A, audit)
      • Senior executive strategy roles with no operational RCM scope

    Bias toward RECALL: when the title + description genuinely point to RCM
    operations but you're unsure, set relevant = true. Only set false when you
    can clearly see it is one of the reject categories.

    For each posting you are given an `id`, `role`, `title`, and `jd` (job
    description excerpt). Return ONLY a JSON array, one object per posting:

    [
      {
        "id": "<echo the id>",
        "relevant": true | false,
        "rcm_role": "billing" | "coding" | "ar_collections" | "denials" |
                    "prior_auth" | "eligibility" | "charge_capture" |
                    "patient_access" | "other" | null,
        "confidence": <float 0.0-1.0>,
        "reason": "<one short clause>"
      }
    ]

    No prose, no markdown fences — just the JSON array, every id echoed exactly.
""").strip()


class JobRelevance(BaseModel):
    id: str
    relevant: bool = True
    rcm_role: str | None = None
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    reason: str = ""


async def qualify_jobs(
    signals: list[RawSignal], *, gate=None, on_spend=None,
) -> dict[str, JobRelevance]:
    """Return id → verdict for each job_posting signal. Batched Sonnet calls.

    Keyed by `source_external_id`. Missing/failed verdicts are simply absent
    from the map; callers should treat absence as fail-open (keep).

    `gate` (optional async checkpoint) is awaited BETWEEN batches so pause/cancel
    freeze or stop the prefilter mid-way — without it, pausing a jobs run keeps
    paying for prefilter calls. `on_spend(LlmSpend)` is called per batch so the
    (otherwise invisible) prefilter cost can be recorded.
    """
    out: dict[str, JobRelevance] = {}
    batches = [signals[i:i + _BATCH_SIZE] for i in range(0, len(signals), _BATCH_SIZE)]
    for i, batch in enumerate(batches):
        # Block while paused / stop when cancelled, before spending on this batch.
        if gate is not None and not await gate():
            logger.info("job qualifier stopped by run gate after %d/%d batches",
                        i, len(batches))
            break
        try:
            verdicts, spend = await _qualify_batch(batch)
            out.update(verdicts)
            if on_spend is not None and spend is not None:
                try:
                    on_spend(spend)
                except Exception:  # noqa: BLE001 — accounting must not break the run
                    logger.exception("job-qualifier on_spend hook failed")
        except Exception as e:  # noqa: BLE001 — a bad batch must not fail the run
            logger.warning("job qualifier batch failed (%s) — keeping %d postings",
                           e, len(batch))
    return out


async def _qualify_batch(batch: list[RawSignal]):
    items = [{
        "id": s.source_external_id,
        "role": s.payload.get("role"),
        "title": s.payload.get("job_title"),
        "jd": (s.payload.get("description") or "")[:_JD_CHARS],
    } for s in batch]

    user_message = (
        "Classify these job postings. Return the JSON array described in the "
        "system prompt, echoing each id.\n\n```json\n"
        + json.dumps(items, indent=1, default=str) + "\n```"
    )

    response = await llm.call_plain(
        system=SYSTEM_PROMPT, user_message=user_message,
        max_tokens=_MAX_TOKENS, model=_MODEL,
    )
    spend = llm.spend_from_response(response, model=_MODEL)
    rows = llm.parse_json_array(llm.extract_text(response))

    verdicts: dict[str, JobRelevance] = {}
    for row in rows:
        if not isinstance(row, dict) or "id" not in row:
            continue
        try:
            v = JobRelevance(**row)
        except Exception:  # noqa: BLE001 — skip a malformed row, keep the rest
            continue
        verdicts[v.id] = v
    return verdicts, spend


async def filter_job_signals(
    signals: list[RawSignal], *, gate=None, on_spend=None,
) -> list[RawSignal]:
    """Pipeline pre-filter: drop job_posting signals the JOB qualifier rejects.

    Non-job signals pass through untouched. Job signals are qualified in
    batches; a posting is dropped only on a confident `relevant=false`
    verdict — missing verdicts and errors keep the posting (fail-open), since
    the company/ICP qualifier downstream is the real backstop.

    `gate`/`on_spend` are threaded to `qualify_jobs` so a paused run freezes the
    prefilter and its spend is recorded.
    """
    jobs = [s for s in signals if s.signal_type == "job_posting"]
    if not jobs:
        return signals

    verdicts = await qualify_jobs(jobs, gate=gate, on_spend=on_spend)

    kept: list[RawSignal] = []
    dropped = 0
    for s in signals:
        if s.signal_type != "job_posting":
            kept.append(s)
            continue
        v = verdicts.get(s.source_external_id)
        if v is not None and not v.relevant:
            dropped += 1
            logger.info("  job-filter ✗ %s — %s (%s)",
                        s.payload.get("job_title"), v.reason, s.company_name_raw)
            continue
        kept.append(s)

    logger.info("job qualifier: kept %d / %d postings (dropped %d)",
                len(jobs) - dropped, len(jobs), dropped)
    return kept
