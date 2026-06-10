"""Smart spend failsafe + cost-event logging.

The monthly budget (budget.py) only gates the START of work. This adds the two
guards it can't give you, layered ON TOP of it (never replacing it):

  - Per-account spike: a single account that runs away (normal ~$0.10, cap $10)
    is dropped to 'error' and stops costing more LLM; the batch keeps going.
  - Per-operation envelope: a batch whose actual spend blows past its estimate
    (default 1.4x) or a hard cap is stopped mid-flight, not after the money is
    gone.

Every paid step records a cost_event, so spend (including discovery qualify) is
auditable rather than invisible. Persistence failures never break scoring - the
guard degrades to in-memory accounting.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from auto_search.models import CompanyCandidate

logger = logging.getLogger(__name__)


def _f(name: str, default: str) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


# Read at call time so tests + env changes take effect without re-import.
def max_per_account() -> float:    return _f("SPEND_MAX_PER_ACCOUNT_USD", "10")
def overrun_ratio() -> float:      return _f("SPEND_OP_OVERRUN_RATIO", "1.4")
def max_op_estimate() -> float:    return _f("SPEND_MAX_OP_ESTIMATE_USD", "150")
def op_hard_cap() -> float:        return _f("SPEND_OP_HARD_CAP_USD", "200")
def daily_warn() -> float:         return _f("SPEND_DAILY_WARN_USD", "50")
def daily_cap() -> float:          return _f("SPEND_DAILY_CAP_USD", "0")
def discovery_est_qual_cost() -> float: return _f("DISCOVERY_EST_QUAL_COST", "0.12")
def discovery_monthly_budget() -> float: return _f("DISCOVERY_MONTHLY_BUDGET", "50")


def social_webhook_max_qualify() -> int:
    """Max NEW companies one social-webhook request may pay to qualify.

    The Trigify webhook is an external, paid path: without a per-request cap a
    huge (or malicious) batch could fire hundreds of LLM qualifications. New-
    company qualifications above this are skipped (already-known companies still
    get their signal appended for free). Tunable via SOCIAL_WEBHOOK_MAX_QUALIFY.
    """
    try:
        return max(1, int(os.getenv("SOCIAL_WEBHOOK_MAX_QUALIFY", "25")))
    except (TypeError, ValueError):
        return 25


def make_social_gate(scoring_repo) -> tuple:
    """The ONE budget gate shared by every paid social path (webhook, on-demand
    run, cron). Returns (gate, cap, est, blocked_now):

      gate()      -> None to allow a NEW qualification (and counts it), or a
                     skip-reason str ("request_cap" / "budget_blocked").
      cap         -> max new qualifications this request/run may pay for.
      est         -> estimated $ per qualification (for the projection).
      blocked_now -> True if the monthly budget is already spent (refuse up front).

    Counting is increment-on-allow: one gate-pass == one committed paid attempt,
    so all three callers account for spend identically (the divergence a review
    flagged). A failed attempt still consumed budget intent — that's the safe
    direction for a cost guard.
    """
    est = discovery_est_qual_cost()
    cap = social_webhook_max_qualify()
    monthly = discovery_monthly_budget()
    try:
        spent = float((scoring_repo.spend_rollup() or {}).get("month_discovery_cost") or 0)
    except Exception:  # noqa: BLE001 — never let the meter block work by erroring
        logger.exception("discovery spend lookup failed; allowing within cap")
        spent = 0.0
    state = {"n": 0}

    def gate() -> str | None:
        if state["n"] >= cap:
            return "request_cap"
        if monthly and spent + (state["n"] + 1) * est > monthly:
            return "budget_blocked"
        state["n"] += 1
        return None

    blocked_now = bool(monthly and spent >= monthly)
    return gate, cap, est, blocked_now


def discovery_manual_default_limit() -> int:
    """Per-source company cap applied to a MANUAL panel run when none is given.

    A manual run must never be silently unlimited — that is how a single click
    turns into dozens of paid website qualifications. The scheduled cron is the
    only place that does a deliberate full-window pull (`--no-limit`); the API
    always falls back to this cap unless the caller passes an explicit limit (or
    an explicit no_cap flag). Tunable via DISCOVERY_MANUAL_DEFAULT_LIMIT.
    """
    try:
        return max(1, int(os.getenv("DISCOVERY_MANUAL_DEFAULT_LIMIT", "10")))
    except (TypeError, ValueError):
        return 10


def record_company_qualify(op: Operation, candidate: CompanyCandidate) -> None:
    """Persist measured qualify spend for one company (real tokens, not estimate).

    Rules-only pre-filters have no llm_spend → $0. LLM paths must attach
    llm_spend on QualificationResult before this is called.
    """
    spend = candidate.qualification.llm_spend
    actual = round(float(spend.cost_usd), 6) if spend else 0.0
    meta = None
    if spend and (spend.input_tokens or spend.output_tokens):
        meta = {
            "input_tokens": spend.input_tokens,
            "output_tokens": spend.output_tokens,
            "measured": True,
        }
    est = discovery_est_qual_cost() if actual > 0 else 0.0
    op.record(
        step="qualify",
        actual_usd=actual,
        company_key=candidate.company_key,
        estimated_usd=est,
        model=spend.model if spend else "",
        searches=spend.searches if spend else 0,
        metadata=meta,
    )
    op.accounts_done += 1


class OverheatError(RuntimeError):
    """An operation's actual spend ran past its envelope."""

    def __init__(self, op: Operation) -> None:
        self.op = op
        super().__init__(
            f"Operation overheated: spent ${op.actual:.2f} vs est ${op.estimated:.2f}")


class Operation:
    """One paid operation (a batch, a single score, a dossier, a cron run).

    Tracks running spend in memory and mirrors it to the repo. The caller checks
    `account_over_cap()` after each account's paid step (Layer A) and
    `overheated()` after each account completes (Layer B).
    """

    def __init__(self, repo, op_type: str, *, estimated_usd: float,
                 accounts_planned: int = 0, metadata: dict | None = None) -> None:
        self.repo = repo
        self.id = "op_" + uuid.uuid4().hex[:16]
        self.op_type = op_type
        self.estimated = round(float(estimated_usd or 0), 4)
        self.accounts_planned = accounts_planned
        self.actual = 0.0
        self.accounts_done = 0
        self.per_account: dict[str, float] = {}
        self.status = "running"
        self.metadata = metadata
        self._persist("create_spend_operation", self._row())

    def _row(self) -> dict:
        return {
            "id": self.id, "op_type": self.op_type, "status": self.status,
            "estimated_usd": self.estimated, "actual_usd": round(self.actual, 4),
            "accounts_planned": self.accounts_planned,
            "accounts_done": self.accounts_done, "metadata": self.metadata,
        }

    def record(self, *, step: str, actual_usd: float, account_id: str | None = None,
               company_key: str | None = None, estimated_usd: float = 0.0,
               model: str = "", searches: int = 0,
               metadata: dict | None = None) -> None:
        """Record one paid step (score|qa|dossier|qualify) and accumulate."""
        amt = round(float(actual_usd or 0), 6)
        self.actual = round(self.actual + amt, 6)
        if account_id:
            self.per_account[account_id] = round(self.per_account.get(account_id, 0.0) + amt, 6)
        self._persist("record_cost_event", {
            "id": "ce_" + uuid.uuid4().hex[:16], "operation_id": self.id,
            "op_type": self.op_type, "account_id": account_id,
            "company_key": company_key, "step": step,
            "estimated_usd": round(float(estimated_usd or 0), 4), "actual_usd": amt,
            "model": model, "searches": int(searches or 0),
            "metadata": metadata,
        })

    def account_cost(self, account_id: str) -> float:
        return self.per_account.get(account_id, 0.0)

    def account_over_cap(self, account_id: str) -> bool:
        return self.account_cost(account_id) > max_per_account()

    def overheated(self) -> bool:
        # estimated == 0 means "no envelope was estimated" (a news refresh, an
        # uncapped pull) — only the absolute hard cap applies there. Comparing
        # against 0 * ratio would brand ANY nonzero spend as overheated.
        if self.estimated > 0 and self.actual > self.estimated * overrun_ratio():
            return True
        return self.actual > op_hard_cap()

    def finish(self, status: str | None = None, error: str | None = None) -> None:
        self.status = status or ("overheated" if self.overheated() else "completed")
        if self.repo is not None and hasattr(self.repo, "finish_spend_operation"):
            try:
                self.repo.finish_spend_operation(
                    self.id, status=self.status, actual_usd=round(self.actual, 4),
                    accounts_done=self.accounts_done, error=error)
            except Exception:  # noqa: BLE001 — accounting must not break scoring
                logger.exception("finish_spend_operation failed for %s", self.id)

    def _persist(self, method: str, payload: dict) -> None:
        fn = getattr(self.repo, method, None) if self.repo is not None else None
        if fn is None:
            return
        try:
            fn(payload)
        except Exception:  # noqa: BLE001 — accounting must not break scoring
            logger.exception("%s failed for %s", method, self.id)


def estimate_batch(n: int, per_account: float) -> float:
    return round(max(0, n) * float(per_account or 0), 4)


def needs_confirmation(estimated_usd: float) -> bool:
    """True when an operation's estimate is large enough to require an explicit
    confirm_large_spend from the caller."""
    return float(estimated_usd or 0) > max_op_estimate()
