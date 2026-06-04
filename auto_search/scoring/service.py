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

from auto_search.scoring import dossier, engine, qa
from auto_search.scoring.frameworks import FRAMEWORKS, framework_for_segment, resolve_tier
from auto_search.scoring.models import Account, Dimension, QAResult, ScoreResult

logger = logging.getLogger(__name__)


class ScoringService:
    def __init__(self, repo) -> None:
        self._repo = repo

    # ── enqueue ────────────────────────────────────────────────────────

    def enqueue_discovery(self, company: dict[str, Any], *, state: str = "scoring") -> dict:
        """Create/refresh an account from a promoted discovery company."""
        return self._repo.upsert_account(_account_from_discovery(company), state=state)

    def enqueue_csv(self, accounts: list[Account], *, state: str = "scoring",
                    import_label: str | None = None) -> list[dict]:
        """Create/refresh imported accounts, tagged with the import they arrived
        on so the batch can be filtered + exported later. Returns the stored rows."""
        return [self._repo.upsert_account(a, state=state, import_label=import_label)
                for a in accounts]

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
        self._repo.set_phase(account_id, "scoring")
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

        # Everything past the engine call (tier resolve, QA, persist) is wrapped
        # so a failure here lands the account in 'error', never leaving it pinned
        # in 'scoring'. A process restart is the only remaining orphan path, and
        # the repository's startup sweep covers that.
        try:
            # Guarantee the tier is consistent with the framework + total at save
            # time, independent of how the score was produced.
            band = resolve_tier(fw, score.total, [d.model_dump() for d in score.dimensions])
            score.tier_band, score.tier_label = band.band, band.label

            qa_result, qa_cost = await self._verify(account_id, account, score, fw)
            score.qa = qa_result
            score.cost_usd = round(score.cost_usd + qa_cost, 4)
            saved = self._repo.save_score(account_id, score)
        except Exception as e:  # noqa: BLE001 — never leave an account stuck 'scoring'
            logger.exception("persisting score failed for %s", account.name)
            self._repo.set_state(account_id, "error", error=f"{type(e).__name__}: {e}")
            return self._repo.get(account_id)

        logger.info("scored %s -> %s %d/%d (QA: %s, $%.3f)",
                    account.name, score.tier_label, score.total, score.max_total,
                    score.qa.status if score.qa else "—", score.cost_usd)
        return saved

    async def _verify(self, account_id, account, score, fw) -> tuple[QAResult, float]:
        """Decide how much independent QA an account earns, then run it.

        Spend the verification budget where it matters, never on accounts no one
        will demo:
          - CSV imports: skip (Definitive firmographics are authoritative).
          - High fit: full independent QA (every checkable fact).
          - Medium fit: a focused QA (net patient revenue + EMR/RCM vendor only).
          - Low fit / not a fit: skip (mark 'skipped', re-score on demand if it
            ever needs to go to leadership).
        QA never sees the scorer's reasoning, so independence is preserved.
        """
        if account.source == "csv":
            return QAResult(
                status="skipped",
                notes="Firmographics taken as authoritative from the Definitive "
                      "import; independent QA skipped to save cost.",
                corrections=[],
            ), 0.0

        band = score.tier_band
        if band == "high":
            self._repo.set_phase(account_id, "verifying")
            return await qa.qa_account(account, score, fw, depth="full")
        if band == "medium":
            self._repo.set_phase(account_id, "verifying")
            return await qa.qa_account(account, score, fw, depth="light")
        return QAResult(
            status="skipped",
            notes=f"{score.tier_label or 'Low-fit'} account; independent QA "
                  "skipped to save cost. Re-score to verify before routing.",
            corrections=[],
        ), 0.0

    # ── dossier (on-demand deep research) ──────────────────────────────

    async def generate_dossier(self, account_id: str) -> dict | None:
        """Generate the landing-page dossier for a scored account end to end.

        Never raises — a failure lands dossier_state='error' (retryable) rather
        than crashing the background task. Only scored accounts qualify.
        """
        row = self._repo.get(account_id)
        if row is None or row.get("state") != "scored":
            return None
        account = _account_from_row(row)
        score = _score_from_row(row)

        self._repo.set_dossier_state(account_id, "generating")
        try:
            result, _cost = await dossier.generate(account, score)
        except dossier.DossierError as e:
            logger.error("dossier failed for %s: %s", account.name, e)
            self._repo.set_dossier_state(account_id, "error", error=str(e))
            return self._repo.get(account_id)
        except Exception as e:  # noqa: BLE001 — any failure is retryable
            logger.exception("unexpected dossier error for %s", account.name)
            self._repo.set_dossier_state(account_id, "error",
                                         error=f"{type(e).__name__}: {e}")
            return self._repo.get(account_id)

        saved = self._repo.save_dossier(account_id, result)
        logger.info("dossier ready for %s ($%.3f)", account.name, result.cost_usd)
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

    Discovery already ran Sonnet + web_search to qualify this company, so carry
    that research forward as authoritative known facts (company type, the ICP
    reasoning, the evidence URL). The scorer then spends its search budget only
    on what discovery did not establish - competitor/RCM vendor, pain, intent -
    instead of re-researching firmographics. This is the single biggest cost cut
    on a promoted account, with no quality loss.
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
        firmographics=_discovery_known_facts(c),
        discovery_signals=signals,
    )


def _discovery_known_facts(c: dict[str, Any]) -> dict[str, Any]:
    """The qualification research, shaped as authoritative facts for the scorer."""
    facts: dict[str, Any] = {}
    ctype = c.get("company_type")
    if ctype and ctype != "unknown":
        facts["Company type"] = ctype
    if c.get("sub_segment"):
        facts["Sub-segment"] = c["sub_segment"]
    if c.get("reasoning"):
        facts["Discovery qualification"] = c["reasoning"]
    if c.get("evidence_url"):
        facts["Evidence URL"] = c["evidence_url"]
    return facts


def _score_from_row(row: dict[str, Any]) -> ScoreResult:
    """Reconstruct the score from a stored row, for the dossier's context."""
    dims = [Dimension(**d) for d in (row.get("dimensions") or [])]
    return ScoreResult(
        account_id=row["account_id"],
        framework=row.get("framework") or "",
        framework_version="",
        dimensions=dims,
        total=row.get("total") or 0,
        max_total=row.get("max_total") or 0,
        tier_band=row.get("tier_band") or "low",
        tier_label=row.get("tier_label") or "",
        recommendation=row.get("recommendation") or "",
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
