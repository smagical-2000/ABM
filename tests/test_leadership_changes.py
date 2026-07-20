"""Tests for the SignalBase leadership-changes connector — no live API calls.

A fake client returns canned JobChangeRecords so the connector's filtering and
mapping are tested deterministically and for free.
"""

from datetime import UTC, datetime

import pytest

from auto_search.clients.signalbase import JobChangeRecord
from auto_search.connectors.leadership_changes import (
    LeadershipChangesConnector,
    _is_target_title,
    _record_to_signal,
    _signal_strength,
    _since_to_preset,
)
from auto_search.healthcare import is_healthcare_provider as _is_healthcare

SINCE = datetime(2026, 3, 1, tzinfo=UTC)


def _rec(**over) -> JobChangeRecord:
    base = dict(
        signalId="sig-1",
        occurredAt="2026-04-15T08:20:03.000Z",
        personName="Jane Doe",
        personLinkedinUrl="https://linkedin.com/in/janedoe",
        newRole="Chief Financial Officer",
        companyName="Acme Health System",
        companyWebsite="acmehealth.org",
        companyIndustry="Hospitals and Health Care",
        companyCountry="US",
        companyEmployeeCount=2400,
    )
    base.update(over)
    return JobChangeRecord(**base)


class FakeClient:
    def __init__(self, records):
        self._records = records
        self.kwargs = None

    async def iter_job_changes(self, **kwargs):
        self.kwargs = kwargs
        for r in self._records:
            yield r


class TestHealthcareGate:
    def test_provider_industries_pass(self):
        for ind in ("Hospitals and Health Care", "Medical Practices",
                    "Mental Health Care", "Home Health Care"):
            assert _is_healthcare(ind), ind

    def test_life_sciences_excluded(self):
        # Pharma/biotech/device are ICP disqualifiers, even though "health"-ish.
        for ind in ("Pharmaceutical Manufacturing", "Biotechnology Research",
                    "Medical Device", "Research Services"):
            assert not _is_healthcare(ind), ind

    def test_non_healthcare_excluded(self):
        for ind in ("Financial Services", "Law Practice", "Retail", None, ""):
            assert not _is_healthcare(ind)

    def test_hospitality_not_matched_as_hospital(self):
        # Regression: "Hospitality" contains the substring "hospital" — must
        # NOT be treated as healthcare (was letting resorts/bars through).
        assert not _is_healthcare("Hospitality")


class TestTargetTitle:
    def test_csuite_always_passes(self):
        for role in ("Chief Financial Officer", "Chief Medical Officer",
                     "Chief Nursing Officer", "CEO"):
            assert _is_target_title(role)

    def test_revenue_and_finance_pass(self):
        assert _is_target_title("Director, Revenue Integrity")
        assert _is_target_title("VP, Finance")

    def test_population_health_passes(self):
        assert _is_target_title("Director of Population Health")

    def test_facilities_fails(self):
        assert not _is_target_title("Director of Facilities")
        assert not _is_target_title("VP, Consultant Relations")

    def test_coordinator_and_cook_not_matched_as_coo(self):
        # Regression: " coo" was matching "COOrdinator" and "COOk". The
        # space-bounded " coo " marker must not.
        assert not _is_target_title("Event Coordinator")
        assert not _is_target_title("Clinical Research Coordinator")
        assert not _is_target_title("MACS Cook III")

    def test_standalone_coo_still_matches(self):
        assert _is_target_title("COO")
        assert _is_target_title("Group COO, Operations")

    def test_junior_revenue_cycle_roles_dropped(self):
        # "revenue cycle" (a positions filter term) matches these, but they're
        # not leadership — drop analysts/reps/leads.
        assert not _is_target_title("Revenue Cycle Data Analyst")
        assert not _is_target_title("Revenue Cycle Management Team Lead")
        assert not _is_target_title("junior revenue cycle representative")

    def test_revenue_cycle_leaders_kept(self):
        assert _is_target_title("Head of Revenue Cycle Management")
        assert _is_target_title("Director, Revenue Cycle")

    def test_assistant_chief_nursing_officer_kept(self):
        # C-suite check wins even though "assistant" is a non-leader marker.
        assert _is_target_title("Assistant Chief Nursing Officer")


class TestRecordToSignal:
    def test_clean_mapping(self):
        sig, reason = _record_to_signal(_rec(), SINCE)
        assert sig is not None, reason
        assert sig.signal_type == "leadership_change"
        assert sig.company_name_raw == "Acme Health System"
        assert sig.company_domain_raw == "acmehealth.org"
        assert sig.company_key == "acmehealthsystem"
        assert sig.source_external_id == "sig-1"
        assert sig.observed_at == datetime(2026, 4, 15, 8, 20, 3, tzinfo=UTC)
        assert sig.payload["new_role"] == "Chief Financial Officer"

    def test_before_window_dropped(self):
        sig, reason = _record_to_signal(
            _rec(occurredAt="2026-01-01T00:00:00.000Z"), SINCE)
        assert sig is None and reason == "before_window"

    def test_non_healthcare_dropped(self):
        sig, reason = _record_to_signal(_rec(companyIndustry="Retail"), SINCE)
        assert sig is None and reason == "not_healthcare"

    def test_pharma_dropped(self):
        sig, reason = _record_to_signal(
            _rec(companyIndustry="Pharmaceutical Manufacturing"), SINCE)
        assert sig is None and reason == "not_healthcare"

    def test_non_target_role_dropped(self):
        sig, reason = _record_to_signal(_rec(newRole="Director of Facilities"), SINCE)
        assert sig is None and reason == "role_not_targeted"

    def test_missing_company_dropped(self):
        sig, reason = _record_to_signal(_rec(companyName=""), SINCE)
        assert sig is None and reason == "missing_company"

    def test_vanity_domain_dropped_to_none(self):
        # SignalBase sometimes returns 'co.jll/4ozae4w' — not a usable domain.
        sig, _ = _record_to_signal(_rec(companyWebsite="co.jll/4ozae4w"), SINCE)
        assert sig.company_domain_raw is None


class TestSignalStrength:
    def test_csuite_strongest(self):
        assert _signal_strength("Chief Financial Officer") == 0.90
        assert _signal_strength("Vice President, Finance") == 0.75
        assert _signal_strength("Director, Revenue Integrity") == 0.65


class TestSincePreset:
    def test_maps_windows(self):
        now = datetime.now(UTC)
        from datetime import timedelta
        assert _since_to_preset(now - timedelta(days=1)) == "today"
        assert _since_to_preset(now - timedelta(days=20)) == "last_30d"
        assert _since_to_preset(now - timedelta(days=80)) == "last_90d"


@pytest.mark.asyncio
async def test_connector_filters_and_stops_at_cutoff():
    records = [
        _rec(signalId="a", newRole="Chief Financial Officer"),          # keep
        _rec(signalId="b", companyIndustry="Retail"),                   # drop: industry
        _rec(signalId="c", newRole="Director of Facilities"),           # drop: role
        _rec(signalId="d", newRole="Chief Medical Officer",
             companyName="Beacon Behavioral",
             companyIndustry="Mental Health Care"),                     # keep
        _rec(signalId="e", occurredAt="2025-01-01T00:00:00Z"),          # cutoff -> STOP
        _rec(signalId="f", newRole="Chief Executive Officer"),          # never reached
    ]
    fake = FakeClient(records)
    connector = LeadershipChangesConnector(client=fake, max_pages=2)

    out = [s async for s in connector.pull(since=SINCE)]
    assert {s.company_name_raw for s in out} == {"Acme Health System", "Beacon Behavioral"}
    # Record 'f' is after the cutoff record 'e' and must not be yielded.
    assert "f" not in {s.source_external_id for s in out}
    # Server filters were forwarded — positions is the primary narrowing.
    assert fake.kwargs["countries"] == "US"
    assert "chief financial officer" in fake.kwargs["positions"]
