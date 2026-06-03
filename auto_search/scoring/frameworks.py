"""Scoring frameworks — one rubric per ICP segment.

Pure configuration and logic, no I/O. Each framework is data: its dimensions,
their point ceilings, the tier bands, and the rubric guidance Claude scores
against. The engine is generic over this config, so adding or tuning a rubric
never touches the engine.

The same definitions are served to the UI (GET /api/scoring/frameworks), so the
dashboard's score bars and tier badges can never drift from what the scorer
actually used.

Three frameworks:
  health_system  27 pts, 6 dimensions (NPR-led, "small is good")
  specialty      30 pts, 3 dimensions (firmographic / technographic / intent)
  payer          30 pts, 3 dimensions (firmographic / technographic / intent)
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Dimension:
    key: str
    label: str
    max: int
    guidance: str          # how Claude should score this dimension


@dataclass(frozen=True)
class Band:
    band: str              # high | medium | low | out  (drives UI color)
    label: str             # "Tier 1" | "High Fit" | ...
    min: int               # inclusive lower bound on total


@dataclass(frozen=True)
class Framework:
    key: str
    label: str
    version: str
    max_total: int
    dimensions: tuple[Dimension, ...]
    bands: tuple[Band, ...]              # evaluated high->low; first match wins
    intro: str                          # one-line rubric framing for the prompt
    auto_tier_out: str | None = field(default=None)  # dim key that forces Tier 4 at 0

    def dimension(self, key: str) -> Dimension | None:
        return next((d for d in self.dimensions if d.key == key), None)


# ── the three rubrics ─────────────────────────────────────────────────

_HEALTH_SYSTEM = Framework(
    key="health_system",
    label="Health System",
    version="hs-2026.2",
    max_total=27,
    intro=("Score a US health system as an ABM target. ICP modeled on Beacon "
           "Health. Prioritize $2B net patient revenue and under — small is good."),
    auto_tier_out="npr",
    dimensions=(
        Dimension("npr", "Net Patient Revenue", 10, textwrap.dedent("""
            $1.0B-$2.0B = 10; $500M-$999M = 8; $200M-$499M = 6; under $200M = 4;
            $2.01B-$2.5B = 4; $2.51B-$3.5B = 2; over $3.5B = 0 (auto Tier 4).
            Sub-$2B is the sweet spot.""").strip()),
        Dimension("emr", "EMR Compatibility", 5, textwrap.dedent("""
            Any non-Epic (Cerner, MEDITECH, Allscripts, athena, eCW, NextGen) = 5;
            unknown/mixed = 3; Epic = 0.""").strip()),
        Dimension("competitor", "Competitor Landscape", 4, textwrap.dedent("""
            Uses Notable or AssortHealth (category buyer) = 4; UiPath/Automation
            Anywhere/Blue Prism (general automation) = 3; Palantir or custom AI = 2;
            no automation/AI vendor found = 3; ThoughtfulAI or a direct RCM
            competitor deployed = 0. Direct competitor takes precedence.""").strip()),
        Dimension("pain", "Pain Point Signals", 5, textwrap.dedent("""
            +1 each (max 5): staffing shortages; rising costs / negative margins;
            rising clinical or claim denials; prior-auth backlogs / manual
            workflows; multi-site billing complexity (expansion, M&A, multi-state).
            """).strip()),
        Dimension("ai_readiness", "AI & Tech Readiness", 2, textwrap.dedent("""
            +1 each (max 2): uses non-competing AI or publishes case studies
            (Palantir, UiPath, Notable, etc.); has a digital-transformation
            initiative, a recent CDO/VP Innovation hire, or a stated AI strategy.
            """).strip()),
        Dimension("leadership", "Leadership Changes", 1,
                  "New CIO, CFO, COO, or CEO in the last 12 months = 1; else 0."),
    ),
    bands=(
        Band("high", "Tier 1", 22),
        Band("medium", "Tier 2", 18),
        Band("low", "Tier 3", 15),
        Band("out", "Tier 4", 0),
    ),
)

_SPECIALTY = Framework(
    key="specialty",
    label="Specialty",
    version="sp-2026.1",
    max_total=30,
    intro=("Score a specialty practice or physician group (ortho, behavioral "
           "health, PT, ASC, and similar) as an ABM target."),
    dimensions=(
        Dimension("firmographic", "Firmographic Fit", 10, textwrap.dedent("""
            Size, number of locations/providers, estimated annual revenue, growth
            indicators (expansion, hiring, funding), and specialty fit.""").strip()),
        Dimension("technographic", "Technographic Fit", 10, textwrap.dedent("""
            EHR/PM/RCM systems, cloud vs legacy, digital adoption, known workflow
            inefficiencies or tech gaps, signs of upcoming modernization.""").strip()),
        Dimension("intent", "Business Priorities & Intent", 10, textwrap.dedent("""
            Hiring patterns (revenue cycle, operations, IT), leadership changes,
            new facilities/expansions, efficiency/margin mandates, press on AI /
            cost reduction / staffing, funding rounds, PE-backed (add points).
            """).strip()),
    ),
    bands=(
        Band("high", "High Fit", 24),
        Band("medium", "Medium Fit", 18),
        Band("low", "Low Fit", 0),
    ),
)

_PAYER = Framework(
    key="payer",
    label="Payer",
    version="py-2026.1",
    max_total=30,
    intro=("Score a health plan or managed-care organization as an ABM target. "
           "Require 200k+ lives. Exclude the top-5 nationals (UnitedHealthcare, "
           "Elevance/Anthem, CVS/Aetna, Cigna, Humana) unless a regional "
           "subsidiary shows strong signals."),
    dimensions=(
        Dimension("firmographic", "Firmographic", 10, textwrap.dedent("""
            Size, revenue, complexity, growth; estimated lives covered (200k+),
            nationwide vs regional scope, plan type.""").strip()),
        Dimension("technographic", "Technographic", 10, textwrap.dedent("""
            Core admin platform, digital maturity, integration needs.""").strip()),
        Dimension("intent", "Intent", 10, textwrap.dedent("""
            Strength and recency (last 24 months) of AI-automation signals:
            partnerships, pilots, exec hires, RFPs, conference talks, public
            statements. Weight pain points: prior-auth backlogs, claims-processing
            cost, member-services volume, CMS interoperability deadlines.""").strip()),
    ),
    bands=(
        Band("high", "Tier 1", 22),
        Band("medium", "Tier 2", 18),
        Band("low", "Tier 3", 0),
    ),
)

FRAMEWORKS: dict[str, Framework] = {
    f.key: f for f in (_HEALTH_SYSTEM, _SPECIALTY, _PAYER)
}

# Discovery segment -> scoring framework. They share keys today, but keep the
# mapping explicit so a discovery segment rename can't silently break scoring.
SEGMENT_TO_FRAMEWORK = {
    "health_system": "health_system",
    "specialty": "specialty",
    "payer": "payer",
}

DEFAULT_FRAMEWORK = "specialty"


def framework_for_segment(segment: str | None) -> Framework:
    """Resolve the rubric for a segment, defaulting to specialty."""
    key = SEGMENT_TO_FRAMEWORK.get((segment or "").strip(), DEFAULT_FRAMEWORK)
    return FRAMEWORKS[key]


def resolve_tier(framework: Framework, total: int,
                 dimensions: list[dict] | None = None) -> Band:
    """Resolve the tier band for a total, honoring any auto-out rule.

    Health systems force Tier 4 when NPR scores 0 (over $3.5B), regardless of
    the rest of the score.
    """
    if framework.auto_tier_out and dimensions:
        d = next((x for x in dimensions if x.get("key") == framework.auto_tier_out), None)
        if d is not None and (d.get("score") or 0) <= 0:
            out = next((b for b in framework.bands if b.band == "out"), None)
            if out is not None:
                return out
    for band in sorted(framework.bands, key=lambda b: b.min, reverse=True):
        if total >= band.min:
            return band
    return framework.bands[-1]


def framework_public(framework: Framework) -> dict:
    """Serialize a framework for the UI (matches scoringData.js FRAMEWORKS)."""
    return {
        "key": framework.key,
        "label": framework.label,
        "version": framework.version,
        "max_total": framework.max_total,
        "dimensions": [
            {"key": d.key, "label": d.label, "max": d.max} for d in framework.dimensions
        ],
        "bands": [
            {"band": b.band, "label": b.label, "min": b.min} for b in framework.bands
        ],
    }


def all_frameworks_public() -> dict[str, dict]:
    return {k: framework_public(f) for k, f in FRAMEWORKS.items()}
