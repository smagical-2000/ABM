"""Typed contracts for scoring.

These mirror the shape the UI consumes (web/discovery/scoringData.js): an
Account is the unit we score; a ScoreResult carries per-dimension scores, the
tier, the recommendation, and the independent QA verdict. Keeping the Python
and JS shapes identical means the API response flows straight into the
components with no mapping layer.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from auto_search.normalize import parse_int_loose

ScoreState = Literal["queued", "scoring", "scored", "error"]
AccountSource = Literal["discovery", "csv"]
QAStatus = Literal["verified", "discrepancy", "unverifiable"]
DimensionFlag = Literal["inferred", "unknown"]


class Account(BaseModel):
    """One company to score. Built from a promoted discovery company or a CSV row."""

    account_id: str
    name: str
    segment: str                              # health_system | specialty | payer
    framework: str                            # resolved rubric key
    source: AccountSource
    domain: str | None = None
    sub_segment: str | None = None
    approximate_employees: int | None = None
    discovery_company_key: str | None = None
    # Structured facts already known (CSV columns or discovery firmographics).
    # Injected into the prompt so the scorer doesn't re-infer what we have, and
    # used by QA as the source of truth.
    firmographics: dict[str, Any] = Field(default_factory=dict)
    # Carried discovery intent (signal summaries) for the intent dimension.
    discovery_signals: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("approximate_employees", mode="before")
    @classmethod
    def _coerce_employees(cls, v: object) -> int | None:
        return parse_int_loose(v)


class Dimension(BaseModel):
    key: str
    label: str
    score: float
    max: int
    summary: str = ""
    flags: list[str] = Field(default_factory=list)   # "inferred" | "unknown"

    @field_validator("score", mode="before")
    @classmethod
    def _coerce_score(cls, v: object) -> float:
        n = parse_int_loose(v)
        return float(n) if n is not None else 0.0


class QACorrection(BaseModel):
    dimension: str
    claimed: str
    found: str
    # QA's corrected score for the dimension. Used to recompute the tier
    # deterministically (tier_changing); the UI renders only claimed/found.
    corrected_score: float | None = None


class QAResult(BaseModel):
    status: QAStatus
    notes: str = ""
    corrections: list[QACorrection] = Field(default_factory=list)
    tier_changing: bool = False


class ScoreResult(BaseModel):
    """A completed score for one account, ready to render or persist."""

    account_id: str
    framework: str
    framework_version: str
    dimensions: list[Dimension]
    total: int
    max_total: int
    tier_band: str
    tier_label: str
    recommendation: str = ""
    qa: QAResult | None = None
    model: str = ""
    scored_at: str | None = None
    # Measured USD spend for this account: scorer call + QA call (0 when QA is
    # skipped). Summed across accounts for the live monthly cost meter.
    cost_usd: float = 0.0

    def clamp(self) -> ScoreResult:
        """Clamp each dimension to its ceiling and recompute the total.

        The model occasionally returns a score above a dimension's max (e.g. a
        3 on a /2 dimension); the rubric ceiling is authoritative, so we never
        let a stray value inflate the tier.
        """
        for d in self.dimensions:
            d.score = max(0.0, min(float(d.score), float(d.max)))
        self.total = int(round(sum(d.score for d in self.dimensions)))
        return self
