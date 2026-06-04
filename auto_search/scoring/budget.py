"""Central spend guard.

The monthly budget must be a rule the server enforces before spending, not just
a number on the dashboard. Every paid path (score, batch, promote-then-score,
dossier) consults this module against the live cost summary before calling
Claude, so neither a UI double-click nor a raw API call can run past the budget.

Estimates are deliberately conservative - the meter records the real spend after
the fact; these only gate whether an operation may start.
"""

from __future__ import annotations

# Conservative per-operation estimates (USD).
EST_SCORE_COST = 0.35    # one account: discovery score + tiered QA
EST_DOSSIER_COST = 0.80  # one deep-research dossier (web_search + Apollo)


class BudgetExceeded(RuntimeError):
    """Raised when a paid operation would exceed the monthly budget."""

    def __init__(self, summary: dict, est: float) -> None:
        self.summary = summary
        self.est = est
        month = summary.get("month_cost", 0) or 0
        budget = summary.get("monthly_budget", 0) or 0
        super().__init__(
            f"Monthly scoring budget reached: ${month:.2f} of ${budget:.0f} used; "
            f"this needs about ${est:.2f}. Raise SCORING_MONTHLY_BUDGET or wait "
            f"for next month."
        )


def remaining(summary: dict) -> float:
    return float(summary.get("budget_remaining", 0) or 0)


def affordable_count(summary: dict, est: float) -> int:
    """How many operations of cost `est` still fit in the month's budget."""
    if est <= 0:
        return 10 ** 9
    return max(0, int(remaining(summary) // est))


def assert_affordable(summary: dict, est: float) -> None:
    """Raise BudgetExceeded if one operation of cost `est` would exceed budget."""
    if remaining(summary) < est:
        raise BudgetExceeded(summary, est)
