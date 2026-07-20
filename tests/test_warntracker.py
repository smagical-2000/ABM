"""Tests for the warntracker connector — especially the external-id collision
fix, which is the difference between keeping and silently dropping signals.
"""

from datetime import UTC, datetime

from auto_search.connectors.warntracker import WarnTrackerConnector, _signal_strength

SINCE = datetime(2026, 1, 1, tzinfo=UTC)


def _row(**overrides):
    row = {
        "Company Name": "Acme Health",
        "# Laid off": 135,
        "Layoff date": "2026-06-27",
        "State": "OH",
        "📍 City/Jurisdiction": "Toledo",
        "companyId": "acme-health",
    }
    row.update(overrides)
    return row


class TestExternalIdCollision:
    """The critical bug: same company + same date at different sites must
    produce DIFFERENT external ids, or the second filing is dropped."""

    def setup_method(self):
        self.c = WarnTrackerConnector()

    def test_same_company_different_cities_distinct_ids(self):
        sig_a, _ = self.c._row_to_signal(
            _row(**{"📍 City/Jurisdiction": "Toledo"}), SINCE)
        sig_b, _ = self.c._row_to_signal(
            _row(**{"📍 City/Jurisdiction": "Cleveland"}), SINCE)
        assert sig_a and sig_b
        assert sig_a.source_external_id != sig_b.source_external_id

    def test_identical_rows_share_id_for_idempotent_reingest(self):
        # Re-scraping the same filing must yield the SAME id (so re-runs are
        # safe no-ops), even as distinct filings stay distinct above.
        sig_a, _ = self.c._row_to_signal(_row(), SINCE)
        sig_b, _ = self.c._row_to_signal(_row(), SINCE)
        assert sig_a.source_external_id == sig_b.source_external_id

    def test_same_company_different_dates_distinct_ids(self):
        sig_a, _ = self.c._row_to_signal(_row(**{"Layoff date": "2026-06-27"}), SINCE)
        sig_b, _ = self.c._row_to_signal(_row(**{"Layoff date": "2026-07-15"}), SINCE)
        assert sig_a.source_external_id != sig_b.source_external_id


class TestStructuralFilters:
    def setup_method(self):
        self.c = WarnTrackerConnector()

    def test_drops_below_min_laid_off(self):
        sig, reason = self.c._row_to_signal(_row(**{"# Laid off": 5}), SINCE)
        assert sig is None and reason == "below_min_laid_off"

    def test_drops_before_window(self):
        sig, reason = self.c._row_to_signal(
            _row(**{"Layoff date": "2025-01-01"}), SINCE)
        assert sig is None and reason == "before_window"

    def test_drops_missing_company(self):
        sig, reason = self.c._row_to_signal(_row(**{"Company Name": ""}), SINCE)
        assert sig is None and reason == "missing_company"

    def test_company_key_present_on_signal(self):
        sig, _ = self.c._row_to_signal(_row(), SINCE)
        assert sig.company_key == "acmehealth"


class TestSignalStrength:
    def test_scales_with_headcount(self):
        assert _signal_strength(600) == 0.85
        assert _signal_strength(250) == 0.75
        assert _signal_strength(60) == 0.65
        assert _signal_strength(20) == 0.55
        assert _signal_strength(None) == 0.55
