"""Tests for the review service — the typed API behind Galyna's panel.

Drives a real JsonFileRepository (tmp file) through the service so the
promote/reject/defer workflow + panel filtering + DTO mapping are verified.
"""

from datetime import UTC, datetime

import pytest

from auto_search.db.repository import JsonFileRepository
from auto_search.models import CompanyCandidate, QualificationResult, RawSignal
from auto_search.services.review import PanelCompany, ReviewService


def _cand(key, name, *, status="qualified", segment="health_system",
          signal_type="leadership_change"):
    return CompanyCandidate(
        company_key=key,
        company_name=name,
        signals=[RawSignal(
            source="signalbase_leadership", source_external_id=f"{key}::1",
            signal_type=signal_type, company_name_raw=name,
            observed_at=datetime(2026, 5, 1, tzinfo=UTC), signal_strength=0.9,
            payload={"new_role": "Chief Financial Officer"},
        )],
        qualification=QualificationResult(
            qualified=(status == "qualified"),
            needs_human_review=(status == "needs_review"),
            confidence=0.88, reasoning="community hospital",
            segment=segment, evidence_url="https://x.org/about",
        ),
    )


@pytest.fixture
def svc(tmp_path):
    repo = JsonFileRepository(tmp_path / "store.json")
    repo.save_candidate(_cand("alpha", "Alpha Health"))
    repo.save_candidate(_cand("bravo", "Bravo Clinic", status="disqualified"))
    repo.save_candidate(_cand("charlie", "Charlie Behavioral", segment="specialty"))
    return ReviewService(repo)


class TestPanel:
    def test_lists_only_qualified_pending(self, svc):
        names = {c.name for c in svc.list_panel()}
        assert names == {"Alpha Health", "Charlie Behavioral"}  # bravo disqualified

    def test_returns_typed_dtos(self, svc):
        panel = svc.list_panel()
        assert all(isinstance(c, PanelCompany) for c in panel)
        alpha = next(c for c in panel if c.name == "Alpha Health")
        assert alpha.segment == "health_system"
        assert alpha.evidence_url == "https://x.org/about"
        assert alpha.signal_count == 1
        assert alpha.signals[0].signal_type == "leadership_change"

    def test_segment_filter(self, svc):
        assert {c.name for c in svc.list_panel(segment="specialty")} == {
            "Charlie Behavioral"
        }

    def test_signal_type_filter(self, svc):
        assert len(svc.list_panel(signal_type="leadership_change")) == 2
        assert len(svc.list_panel(signal_type="acquisition")) == 0


class TestWorkflow:
    def test_promote_removes_from_panel_and_returns_account(self, svc):
        account_id = svc.promote("alpha")
        assert account_id == "stub-account::alpha"
        assert "Alpha Health" not in {c.name for c in svc.list_panel()}
        # The row still exists (ledger), now promoted.
        assert svc.get_company("alpha").review_status == "promoted"

    def test_reject_removes_from_panel_with_reason(self, svc):
        svc.reject("charlie", reason="too small")
        assert "Charlie Behavioral" not in {c.name for c in svc.list_panel()}
        assert svc.get_company("charlie").review_status == "rejected"

    def test_defer_removes_from_panel(self, svc):
        svc.defer("alpha")
        assert svc.get_company("alpha").review_status == "deferred"
        assert "Alpha Health" not in {c.name for c in svc.list_panel()}

    def test_promote_unknown_raises(self, svc):
        with pytest.raises(KeyError):
            svc.promote("does-not-exist")


class TestStats:
    def test_counts_and_panel_pending(self, svc):
        s = svc.stats()
        assert s.qualified == 2 and s.disqualified == 1 and s.total == 3
        assert s.panel_pending == 2
        svc.promote("alpha")
        assert svc.stats().panel_pending == 1   # alpha left the panel
