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
# "skipped" = QA was deliberately not run (CSV trusted, or a low-fit account not
# worth a verification spend). Distinct from "verified" so the UI never implies
# an account was independently checked when it was not.
QAStatus = Literal["verified", "discrepancy", "unverifiable", "skipped"]
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
    # When QA's corrections are applied to the official score, snapshot the
    # analyst's original pass so the UI can show analyst -> official, and mark
    # that the official total/tier reflect the correction.
    applied: bool = False
    applied_at: str | None = None
    analyst_total: int | None = None
    analyst_dimensions: list[Dimension] = Field(default_factory=list)


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


# ── Landing-page dossier ──────────────────────────────────────────────
# A deep-research one-pager generated on demand for an account being pursued.
# It is layered on top of the score (which supplies the fit + pillars), so these
# models cover only the researched sections. Confidence mirrors the source
# document's honesty: "known" facts are cited, "likely" are inferred, "unknown"
# could not be confirmed.

DossierConfidence = Literal["known", "likely", "unknown"]


class FactRow(BaseModel):
    label: str
    value: str
    confidence: DossierConfidence = "known"


class IntentSignal(BaseModel):
    signal: str                  # headline of the signal
    detail: str = ""
    score: int = 0               # 0..10, how strong a buying signal


class DecisionMaker(BaseModel):
    role: str                    # title / function
    contact: str = ""            # person name (from Apollo), or "Unknown ..."
    notes: str = ""              # why they matter to an RCM sale
    linkedin: str = ""           # public LinkedIn URL, when Apollo has it


class NewsItem(BaseModel):
    headline: str
    detail: str = ""
    date: str = ""


class EntryStrategy(BaseModel):
    timing: str = ""             # "HIGH - ...", "MEDIUM - ...", "LOW - ..."
    primary_angles: list[str] = Field(default_factory=list)
    cautions: list[str] = Field(default_factory=list)
    deal_size: str = ""


class Dossier(BaseModel):
    """The researched sections of the landing-page one-pager."""

    firmographic_profile: list[FactRow] = Field(default_factory=list)
    services: list[FactRow] = Field(default_factory=list)
    intent_signals: list[IntentSignal] = Field(default_factory=list)
    decision_makers: list[DecisionMaker] = Field(default_factory=list)
    entry_strategy: EntryStrategy = Field(default_factory=EntryStrategy)
    rcm_complexity: list[FactRow] = Field(default_factory=list)
    recent_news: list[NewsItem] = Field(default_factory=list)
    pain_points: list[str] = Field(default_factory=list)
    messaging_angles: list[str] = Field(default_factory=list)
    model: str = ""
    generated_at: str | None = None
    cost_usd: float = 0.0
