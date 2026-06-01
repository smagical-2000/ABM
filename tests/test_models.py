"""Tests for models.py — segment mapping, status mapping, dedup key."""

from datetime import UTC, datetime

import pytest

from auto_search.models import (
    SEGMENT_TO_SCORER,
    QualificationResult,
    RawSignal,
    to_scorer_segment,
)


def _signal(name: str, **payload) -> RawSignal:
    return RawSignal(
        source="warntracker",
        source_external_id="x",
        signal_type="layoff",
        company_name_raw=name,
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        payload=payload,
    )


class TestSegmentMapping:
    def test_all_discovery_segments_map_to_scorer(self):
        # Guards the promotion boundary: every discovery segment must have a
        # scorer equivalent, or scorer.py --segment will fail.
        assert SEGMENT_TO_SCORER["specialty"] == "specialties"
        assert SEGMENT_TO_SCORER["payer"] == "payer"
        assert SEGMENT_TO_SCORER["health_system"] == "hs"

    def test_to_scorer_segment_helper(self):
        assert to_scorer_segment("health_system") == "hs"

    def test_unknown_segment_raises(self):
        with pytest.raises(ValueError):
            to_scorer_segment("nonsense")  # type: ignore[arg-type]


class TestCompanyKey:
    def test_dedup_key_collapses_legal_variants(self):
        a = _signal("Acme Health LLC")
        b = _signal("Acme Health, Inc.")
        assert a.company_key == b.company_key == "acmehealth"


class TestEmployeeCoercion:
    def test_messy_employee_count_is_coerced(self):
        q = QualificationResult(
            qualified=True, confidence=0.9, reasoning="x",
            approximate_employees="2,400",
        )
        assert q.approximate_employees == 2400


class TestStatusMapping:
    """Status order matters — errors must never look qualified or like
    ordinary review items."""

    def test_qualified(self):
        q = QualificationResult(qualified=True, confidence=0.9, reasoning="x")
        assert q.to_status() == "qualified"

    def test_disqualified(self):
        q = QualificationResult(qualified=False, confidence=0.9, reasoning="x")
        assert q.to_status() == "disqualified"

    def test_needs_review_low_confidence(self):
        q = QualificationResult(
            qualified=True, confidence=0.5, reasoning="x",
            needs_human_review=True,
        )
        assert q.to_status() == "needs_review"

    def test_error_takes_priority_over_review(self):
        # An LLM failure (is_error) must surface as 'error', not 'needs_review',
        # so "website unreachable" doesn't pollute Galyna's real review queue.
        q = QualificationResult(
            qualified=False, confidence=0.0, reasoning="LLM failed",
            needs_human_review=True, is_error=True,
        )
        assert q.to_status() == "error"
