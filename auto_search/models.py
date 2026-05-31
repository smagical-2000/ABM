"""Pydantic models shared across the Auto Search module."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


Segment = Literal["specialty", "payer", "health_system"]
IcpStatus = Literal["pending", "qualified", "needs_review", "disqualified"]


class RawSignal(BaseModel):
    """One signal pulled from one source, before any matching or qualification.

    Every connector returns this shape — the rest of the pipeline doesn't
    care which source it came from.
    """

    source: str                           # e.g. "layoffs_fyi"
    source_external_id: str               # vendor's stable ID; used for dedup
    signal_type: str                      # e.g. "layoff", "funding_round"
    company_name_raw: str
    company_domain_raw: str | None = None
    signal_strength: float = Field(0.5, ge=0.0, le=1.0)
    payload: dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime                 # when the event happened


class QualificationResult(BaseModel):
    """Output of the ICP qualification step (rules + LLM)."""

    qualified: bool
    segment: Segment | None = None
    sub_segment: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    needs_human_review: bool = False
    decided_by: Literal["rules", "llm", "rules+llm"] = "rules+llm"
