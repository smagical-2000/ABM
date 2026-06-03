"""ScoringService — orchestration the API and runner call.

Owns the lifecycle: an account is enqueued (from a promoted discovery company or
a CSV import), scored by the engine, independently QA'd, and persisted. State
transitions (queued -> scoring -> scored / error) live here, not in the engine.

Concurrency is the caller's: `run_scoring` is a coroutine the API backgrounds
with asyncio.create_task so the HTTP response returns immediately and the UI
shows the live "Scoring…" state, then resolves.
"""

from __future__ import annotations

import logging
from typing import Any

from auto_search.scoring import engine, qa
from auto_search.scoring.frameworks import FRAMEWORKS, framework_for_segment, resolve_tier
from auto_search.scoring.models import Account

logger = logging.getLogger(__name__)


class ScoringService:
    def __init__(self, repo) -> None:
        self._repo = repo

    # ── enqueue ────────────────────────────────────────────────────────

    def enqueue_discovery(self, company: dict[str, Any], *, state: str = "scoring") -> dict:
        """Create/refresh an account from a promoted discovery company."""
        return self._repo.upsert_account(_account_from_discovery(company), state=state)

    def enqueue_csv(self, accounts: list[Account], *, state: str = "scoring") -> list[dict]:
        """Create/refresh imported accounts. Returns the stored rows."""
        return [self._repo.upsert_account(a, state=state) for a in accounts]

    # ── score ──────────────────────────────────────────────────────────

    async def run_scoring(self, account_id: str) -> dict | None:
        """Score one account end-to-end: engine -> independent QA -> persist.

        Never raises — a scoring failure lands the account in 'error' (retryable)
        rather than crashing a background task.
        """
        row = self._repo.get(account_id)
        if row is None:
            return None
        account = _account_from_row(row)
        fw = FRAMEWORKS.get(account.framework) or framework_for_segment(account.segment)

        self._repo.set_state(account_id, "scoring")
        try:
            score = await engine.score_account(account)
        except engine.ScoringError as e:
            logger.error("scoring failed for %s: %s", account.name, e)
            self._repo.set_state(account_id, "error", error=str(e))
            return self._repo.get(account_id)
        except Exception as e:  # noqa: BLE001 — defensive: any failure is retryable
            logger.exception("unexpected scoring error for %s", account.name)
            self._repo.set_state(account_id, "error", error=f"{type(e).__name__}: {e}")
            return self._repo.get(account_id)

        # Guarantee the tier is consistent with the framework + total at save
        # time, independent of how the score was produced.
        band = resolve_tier(fw, score.total, [d.model_dump() for d in score.dimensions])
        score.tier_band, score.tier_label = band.band, band.label

        score.qa = await qa.qa_account(account, score, fw)
        saved = self._repo.save_score(account_id, score)
        logger.info("scored %s -> %s %d/%d (QA: %s)",
                    account.name, score.tier_label, score.total, score.max_total,
                    score.qa.status if score.qa else "—")
        return saved

    # ── reads ──────────────────────────────────────────────────────────

    def list_scored(self) -> list[dict]:
        return self._repo.list_accounts()

    def get(self, account_id: str) -> dict | None:
        return self._repo.get(account_id)

    def active(self) -> list[dict]:
        return self._repo.active()

    def exists(self, account_id: str) -> bool:
        return self._repo.exists(account_id)


# ── account construction ──────────────────────────────────────────────


def _account_from_discovery(c: dict[str, Any]) -> Account:
    """Build a scoreable Account from a promoted discovery company.

    Discovery has no structured Definitive facts, so firmographics stays light;
    the signals carry as intent context.
    """
    segment = c.get("segment") or "specialty"
    key = c.get("company_key") or c.get("name", "unknown")
    signals = [
        {"signal_type": s.get("signal_type"), "summary": s.get("summary")}
        for s in (c.get("signals") or [])
    ]
    return Account(
        account_id="acc_" + key,
        name=c.get("name", ""),
        segment=segment,
        framework=framework_for_segment(segment).key,
        source="discovery",
        domain=c.get("domain"),
        sub_segment=c.get("sub_segment"),
        approximate_employees=c.get("approximate_employees"),
        discovery_company_key=c.get("company_key"),
        discovery_signals=signals,
    )


def _account_from_row(row: dict[str, Any]) -> Account:
    """Reconstruct an Account from a stored scored_accounts row (for re-score)."""
    return Account(
        account_id=row["account_id"],
        name=row["name"],
        segment=row.get("segment") or "specialty",
        framework=row.get("framework") or framework_for_segment(row.get("segment")).key,
        source=row.get("source") or "discovery",
        domain=row.get("domain"),
        sub_segment=row.get("sub_segment"),
        approximate_employees=row.get("approximate_employees"),
        discovery_company_key=row.get("discovery_company_key"),
        firmographics=row.get("firmographics") or {},
        discovery_signals=row.get("discovery_signals") or [],
    )
