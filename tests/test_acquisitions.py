"""Tests for the SignalBase acquisitions (M&A) connector — no live API calls.

A fake client returns canned AcquisitionRecords so the connector's filtering
and mapping are tested deterministically and for free.
"""

from datetime import UTC, datetime

import pytest

from auto_search.clients.signalbase import AcquisitionRecord
from auto_search.connectors.acquisitions import (
    AcquisitionsConnector,
    _record_to_signal,
    _signal_strength,
)

SINCE = datetime(2026, 3, 1, tzinfo=UTC)


def _rec(**over) -> AcquisitionRecord:
    base = dict(
        signalId="acq-1",
        occurredAt="2026-05-26T03:56:01.000Z",
        announcedDate="2026-05-26",
        companyName="Cullman Regional Medical Center",
        companyWebsite="cullmanregional.com",
        companyIndustry="Hospitals and Health Care",
        companySubcategory="healthcare",
        companyCountry="US",
        companyEmployeeCount=1200,
        companyDescription="A regional community hospital.",
        acquiringCompanyName="Big Health System",
        acquiringCompanyWebsite="bighealth.org",
        acquiringCompanyIndustry="Hospitals and Health Care",
        amount=250000000,
        currency="$",
        sources=[{"url": "https://example.com/deal", "sourceType": "press_release"}],
    )
    base.update(over)
    return AcquisitionRecord(**base)


class FakeClient:
    def __init__(self, records):
        self._records = records
        self.kwargs = None

    async def iter_acquisitions(self, **kwargs):
        self.kwargs = kwargs
        for r in self._records:
            yield r


class TestRecordToSignal:
    def test_clean_mapping_subject_is_acquired_company(self):
        sig, reason = _record_to_signal(_rec(), SINCE)
        assert sig is not None, reason
        assert sig.signal_type == "acquisition"
        # Subject is the ACQUIRED company, not the acquirer.
        assert sig.company_name_raw == "Cullman Regional Medical Center"
        assert sig.company_domain_raw == "cullmanregional.com"
        assert sig.company_key == "cullmanregionalmedicalcenter"
        assert sig.source_external_id == "acq-1"
        # Acquirer + deal captured in payload.
        assert sig.payload["acquirer_name"] == "Big Health System"
        assert sig.payload["deal_amount_usd"] == 250000000
        assert sig.payload["source_urls"] == ["https://example.com/deal"]
        assert sig.observed_at == datetime(2026, 5, 26, 3, 56, 1, tzinfo=UTC)

    def test_biotech_disqualified_despite_healthcare_label(self):
        # Curevo-style: industry says "Hospitals and Health Care" but the
        # subcategory reveals biotech — must be dropped (ICP disqualifier).
        sig, reason = _record_to_signal(
            _rec(companyName="Curevo Vaccine",
                 companyIndustry="Hospitals and Health Care",
                 companySubcategory="biotechnology"),
            SINCE,
        )
        assert sig is None and reason == "not_healthcare"

    def test_before_window_dropped(self):
        sig, reason = _record_to_signal(
            _rec(occurredAt="2026-01-01T00:00:00.000Z"), SINCE)
        assert sig is None and reason == "before_window"

    def test_falls_back_to_announced_date_when_no_occurred(self):
        sig, _ = _record_to_signal(
            _rec(occurredAt=None, announcedDate="2026-04-10"), SINCE)
        assert sig is not None
        assert sig.observed_at == datetime(2026, 4, 10, tzinfo=UTC)

    def test_non_us_dropped(self):
        sig, reason = _record_to_signal(_rec(companyCountry="GB"), SINCE)
        assert sig is None and reason == "non_us"

    def test_missing_company_dropped(self):
        sig, reason = _record_to_signal(_rec(companyName=""), SINCE)
        assert sig is None and reason == "missing_company"

    def test_vanity_domain_dropped_to_none(self):
        sig, _ = _record_to_signal(_rec(companyWebsite="co.xyz/abc"), SINCE)
        assert sig.company_domain_raw is None


class TestSignalStrength:
    def test_scales_with_acquired_company_size(self):
        assert _signal_strength(_rec(companyEmployeeCount=5000)) == 0.90
        assert _signal_strength(_rec(companyEmployeeCount=300)) == 0.80
        assert _signal_strength(_rec(companyEmployeeCount=80)) == 0.70
        assert _signal_strength(_rec(companyEmployeeCount=10)) == 0.60
        assert _signal_strength(_rec(companyEmployeeCount=None)) == 0.70


@pytest.mark.asyncio
async def test_connector_filters_and_stops_at_cutoff():
    records = [
        _rec(signalId="a"),                                            # keep
        _rec(signalId="b", companySubcategory="biotechnology"),        # drop biotech
        _rec(signalId="c", companyName="Beacon Behavioral",
             companyIndustry="Mental Health Care"),                    # keep
        _rec(signalId="d", occurredAt="2025-01-01T00:00:00Z"),         # cutoff -> STOP
        _rec(signalId="e"),                                            # never reached
    ]
    fake = FakeClient(records)
    connector = AcquisitionsConnector(client=fake, max_pages=1)

    out = [s async for s in connector.pull(since=SINCE)]
    assert {s.company_name_raw for s in out} == {
        "Cullman Regional Medical Center", "Beacon Behavioral"
    }
    assert "e" not in {s.source_external_id for s in out}
    # Server filters were forwarded.
    assert fake.kwargs["countries"] == "US"
    assert "Hospitals and Health Care" in fake.kwargs["categories"]
