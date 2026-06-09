"""Tests for the SignalBase funding connector — no live API calls.

A fake client returns canned FundingRecords so the connector's filtering and
mapping are tested deterministically and for free.
"""

from datetime import UTC, datetime

import pytest

from auto_search.clients.signalbase import FundingRecord
from auto_search.connectors.funding import (
    FundingConnector,
    _record_to_signal,
    _signal_strength,
)

SINCE = datetime(2026, 3, 1, tzinfo=UTC)


def _rec(**over) -> FundingRecord:
    base = dict(
        signalId="fund-1",
        occurredAt="2026-05-20T03:56:01.000Z",
        announcedDate="2026-05-20",
        companyName="Novellia Health",
        companyWebsite="novellia.com",
        companyIndustry="Hospitals and Health Care",
        companySubcategory="healthcare",
        companyCountry="US",
        companyEmployeeCount=300,
        roundType="series a",
        amount=18_000_000,
        currency="USD",
        verificationStatus="verified",
        investors=[{"name": "Vertex Ventures"}],
        sources=[{"url": "https://news.example/round"}],
    )
    base.update(over)
    return FundingRecord(**base)


class FakeClient:
    def __init__(self, records):
        self._records = records
        self.kwargs = None

    async def iter_funding(self, **kwargs):
        self.kwargs = kwargs
        for r in self._records:
            yield r


class TestRecordToSignal:
    def test_clean_provider_round_maps(self):
        sig, reason = _record_to_signal(_rec(), SINCE)
        assert sig is not None, reason
        assert sig.signal_type == "funding_round"
        assert sig.company_name_raw == "Novellia Health"
        assert sig.company_domain_raw == "novellia.com"
        assert sig.payload["amount_usd"] == 18_000_000
        assert sig.payload["investors"] == ["Vertex Ventures"]
        assert sig.summary == "Raised $18,000,000 Series A"

    def test_ai_vendor_dropped_before_qualifier(self):
        # "Mia Health" style: industry healthcare but subcategory=ai → a vendor,
        # not a provider. Must drop WITHOUT reaching the qualifier.
        sig, reason = _record_to_signal(_rec(companySubcategory="ai"), SINCE)
        assert sig is None and reason == "vendor_not_provider"

    def test_biotech_dropped(self):
        sig, reason = _record_to_signal(_rec(companySubcategory="biotechnology"), SINCE)
        assert sig is None and reason == "vendor_not_provider"

    def test_non_us_dropped(self):
        sig, reason = _record_to_signal(_rec(companyCountry="SG"), SINCE)
        assert sig is None and reason == "non_us"

    def test_before_window_dropped(self):
        sig, reason = _record_to_signal(
            _rec(occurredAt="2026-01-01T00:00:00Z"), SINCE)
        assert sig is None and reason == "before_window"

    def test_non_healthcare_industry_dropped(self):
        sig, reason = _record_to_signal(
            _rec(companyIndustry="Financial Services", companySubcategory=None), SINCE)
        assert sig is None and reason == "not_healthcare"


class TestSignalStrength:
    def test_scales_with_round_size(self):
        assert _signal_strength(150_000_000) == 0.90
        assert _signal_strength(30_000_000) == 0.82
        assert _signal_strength(8_000_000) == 0.72
        assert _signal_strength(2_000_000) == 0.62
        assert _signal_strength(None) == 0.65


@pytest.mark.asyncio
async def test_connector_filters_and_stops_at_cutoff():
    records = [
        _rec(signalId="a"),                                      # keep (provider)
        _rec(signalId="b", companySubcategory="saas"),           # drop vendor
        _rec(signalId="c", companyName="Beacon Behavioral",
             companyIndustry="Mental Health Care"),              # keep
        _rec(signalId="d", occurredAt="2025-01-01T00:00:00Z"),   # cutoff -> STOP
        _rec(signalId="e"),                                      # never reached
    ]
    fake = FakeClient(records)
    connector = FundingConnector(client=fake, max_pages=1)

    out = [s async for s in connector.pull(since=SINCE)]
    assert {s.company_name_raw for s in out} == {"Novellia Health", "Beacon Behavioral"}
    assert "e" not in {s.source_external_id for s in out}
    assert fake.kwargs["countries"] == "US"
    assert "Hospitals and Health Care" in fake.kwargs["categories"]
