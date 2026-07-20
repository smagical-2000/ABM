"""Auto-score the leads the lifecycle sweep promoted back out of review.

The sweep (lifecycle.py) flips a re-heated lead needs_review -> qualified and hands
the promoted keys here. For each, IF there's budget headroom we run the same
promote->score flow the Score button triggers; with no headroom we leave the lead
in the qualified queue as Hot for a human to score. Budget-gated and flag-gated on
purpose — this can run unattended from the daily cron.

Toggle with DISCOVERY_AUTOSCORE_ON_PROMOTE (default on); when off, promoted leads
simply rejoin Discovery and wait for a manual Score.
"""

from __future__ import annotations

import logging
import os

from auto_search.scoring import budget as budget_guard
from auto_search.scoring import spend_guard

logger = logging.getLogger(__name__)


def autoscore_enabled() -> bool:
    return os.getenv("DISCOVERY_AUTOSCORE_ON_PROMOTE", "true").strip().lower() not in (
        "0", "false", "no", "off", ""
    )


async def autoscore_promoted(promoted_keys, *, review, scoring, scoring_repo) -> dict:
    """Promote->score each key while budget allows. Returns {"scored", "skipped"}.

    `review` is a ReviewService, `scoring` a ScoringService over `scoring_repo`.
    Skipped keys (flag off or no budget) stay qualified-Hot in Discovery — never
    lost, just left for a human.
    """
    keys = list(promoted_keys)
    if not keys:
        return {"scored": [], "skipped": []}
    if not autoscore_enabled():
        logger.info("autoscore on promote disabled — %d left in queue", len(keys))
        return {"scored": [], "skipped": keys}

    scored: list[str] = []
    skipped: list[str] = []
    for key in keys:
        summary = scoring_repo.cost_summary()
        if budget_guard.remaining(summary) < budget_guard.EST_SCORE_COST:
            skipped.append(key)            # no headroom — stays qualified Hot for manual scoring
            continue
        company = review.get_company(key)
        if company is None:
            continue
        review.promote(key)                # review_status=promoted -> leaves the panel
        row = scoring.enqueue_discovery(company.model_dump(), state="scoring")
        op = spend_guard.Operation(scoring_repo, "promote",
                                   estimated_usd=budget_guard.EST_SCORE_COST,
                                   accounts_planned=1)
        try:
            await scoring.run_scoring(row["account_id"], op=op)
        finally:
            op.finish()
        scored.append(key)

    logger.info("autoscore promoted: %d scored, %d left for manual (budget)",
                len(scored), len(skipped))
    return {"scored": scored, "skipped": skipped}
