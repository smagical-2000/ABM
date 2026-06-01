"""Review service — the typed API behind Galyna's discovery panel.

This is the seam the UI (or a thin FastAPI layer) calls. It returns Pydantic
DTOs (never raw repo dicts) and owns the human review workflow:

    list_panel(filters)        → companies awaiting a decision
    get_company(key)           → one company for the detail drawer
    promote(key)               → mark for scoring (returns a stub account id)
    reject(key, reason)        → drop it, with a reason
    defer(key)                 → snooze it
    stats()                    → dashboard counts

Storage lives behind the DiscoveryRepository protocol, so swapping the JSON
file for Postgres never touches this service or the UI above it.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel

from auto_search.db.repository import DiscoveryRepository

logger = logging.getLogger(__name__)


# ── DTOs (what the UI/API consumes) ───────────────────────────────────


class PanelSignal(BaseModel):
    """One reason a company is in the panel — the 'why discovered' row."""

    source: str
    signal_type: str
    summary: str | None = None
    observed_at: str | None = None
    strength: float | None = None


class PanelCompany(BaseModel):
    """A qualified company as the panel/drawer needs it."""

    company_key: str
    name: str
    segment: str | None = None
    sub_segment: str | None = None
    company_type: str | None = None
    approximate_employees: int | None = None
    confidence: float | None = None
    reasoning: str | None = None
    evidence_url: str | None = None
    domain: str | None = None
    review_status: str = "pending"
    first_seen_at: str | None = None
    signal_count: int = 0
    signals: list[PanelSignal] = []


class DiscoveryStats(BaseModel):
    """Dashboard counts."""

    qualified: int = 0
    needs_review: int = 0
    disqualified: int = 0
    error: int = 0
    total: int = 0
    panel_pending: int = 0      # qualified AND awaiting a decision


# ── service ───────────────────────────────────────────────────────────


class ReviewService:
    """Read + workflow operations over the discovery store, as typed DTOs."""

    def __init__(self, repo: DiscoveryRepository) -> None:
        self._repo = repo

    # -- reads --

    def list_panel(
        self,
        *,
        statuses: tuple[str, ...] = ("qualified",),
        segment: str | None = None,
        signal_type: str | None = None,
    ) -> list[PanelCompany]:
        """Companies of the given verdict status(es) still awaiting a decision.

        Defaults to `qualified`; pass `("needs_review",)` for that tab. Only
        review_status='pending' rows surface — promoted / rejected / deferred
        drop out. Optional filters narrow by segment or signal type.
        """
        rows = self._repo.panel(statuses=statuses)
        out: list[PanelCompany] = []
        for row in rows:
            if row.get("review_status", "pending") != "pending":
                continue
            if segment and row.get("segment") != segment:
                continue
            if signal_type and not _has_signal_type(row, signal_type):
                continue
            out.append(_to_panel_company(row))
        return out

    def get_company(self, company_key: str) -> PanelCompany | None:
        row = self._repo.get(company_key)
        return _to_panel_company(row) if row else None

    def stats(self) -> DiscoveryStats:
        s = self._repo.stats()
        pending = len(self.list_panel())
        return DiscoveryStats(
            qualified=s.get("qualified", 0),
            needs_review=s.get("needs_review", 0),
            disqualified=s.get("disqualified", 0),
            error=s.get("error", 0),
            total=s.get("total", 0),
            panel_pending=pending,
        )

    # -- workflow --

    def promote(self, company_key: str) -> str:
        """Mark a company for scoring and return an account id.

        STUB: until the `accounts` table + domain-first matching exist, this
        records the decision and returns a placeholder id. The real promotion
        (create account, link signals, kick off scorer.py) lands with the
        accounts table — see KNOWN_ISSUES.md.
        """
        row = self._repo.set_review(company_key, "promoted")
        if row is None:
            raise KeyError(f"company not found: {company_key!r}")
        account_id = f"stub-account::{company_key}"
        logger.info("promoted %s → %s (stub account)", company_key, account_id)
        return account_id

    def reject(self, company_key: str, reason: str) -> None:
        if self._repo.set_review(company_key, "rejected", reason=reason) is None:
            raise KeyError(f"company not found: {company_key!r}")

    def defer(self, company_key: str) -> None:
        if self._repo.set_review(company_key, "deferred") is None:
            raise KeyError(f"company not found: {company_key!r}")


# ── mapping helpers ───────────────────────────────────────────────────


def _has_signal_type(row: dict, signal_type: str) -> bool:
    return any(s.get("signal_type") == signal_type for s in row.get("signals", []))


def _to_panel_company(row: dict) -> PanelCompany:
    signals = [
        PanelSignal(
            source=s.get("source", "?"),
            signal_type=s.get("signal_type", "?"),
            summary=s.get("summary"),
            observed_at=s.get("observed_at"),
            strength=s.get("signal_strength"),
        )
        for s in row.get("signals", [])
    ]
    return PanelCompany(
        company_key=row.get("normalized_name", ""),
        name=row.get("display_name", ""),
        segment=row.get("segment"),
        sub_segment=row.get("sub_segment"),
        company_type=row.get("company_type"),
        approximate_employees=row.get("approximate_employees"),
        confidence=row.get("confidence"),
        reasoning=row.get("reasoning"),
        evidence_url=row.get("evidence_url"),
        domain=row.get("domain"),
        review_status=row.get("review_status", "pending"),
        first_seen_at=row.get("first_seen_at"),
        signal_count=len(signals),
        signals=signals,
    )
