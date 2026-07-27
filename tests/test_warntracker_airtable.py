"""WARN source swap (2026-07-27): warntracker.com's frozen API -> the
publisher's live public Airtable share.

The bugs these pin, all observed on the real payload:
  * select cells arrive as choice IDs ("seljnCtFkFOITAF3u"), not "UT"
  * the free view seeds advert rows the pipeline must never treat as notices
  * dates are full ISO instants, not the bare dates the old feed served
  * "# Laid off range" is a RANGE ("101 - 250"), read at its lower bound
  * windowing on Layoff date re-admits years-old filings (the 508-row trap)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from auto_search.connectors import airtable_share
from auto_search.connectors.warntracker import WarnTrackerConnector

NOW = datetime.now(UTC)


def _payload(rows, columns=None):
    """A readSharedViewData envelope shaped like the live one."""
    columns = columns or [
        {"id": "fldC", "name": "Company Name", "type": "text"},
        {"id": "fldS", "name": "State", "type": "select",
         "typeOptions": {"choices": {"selUT": {"name": "UT"},
                                     "selNC": {"name": "NC"}}}},
        {"id": "fldN", "name": "Notice Date", "type": "date"},
        {"id": "fldL", "name": "Layoff date", "type": "date"},
        {"id": "fldR", "name": "# Laid off range", "type": "text"},
        {"id": "fldT", "name": "Layoff Type", "type": "text"},
    ]
    return {"msg": "SUCCESS", "data": {"table": {"columns": columns,
                                                 "rows": rows}}}


def _row(rid, company, state="selNC", notice=None, layoff=None,
         rng="101 - 250", ltype="Permanent"):
    return {"id": rid, "cellValuesByColumnId": {
        "fldC": company, "fldS": state, "fldN": notice, "fldL": layoff,
        "fldR": rng, "fldT": ltype}}


def _iso(days_ago):
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT00:00:00.000Z")


# ── the pure decoder ──────────────────────────────────────────────────


def test_select_choice_ids_resolve_to_labels():
    rows = airtable_share.decode_shared_view_payload(
        _payload([_row("rec1", "Acme Health", state="selUT")]))
    assert rows[0]["State"] == "UT"          # not "selUT"
    assert rows[0]["Company Name"] == "Acme Health"
    assert rows[0]["_airtable_row_id"] == "rec1"


def test_unknown_choice_id_passes_through_instead_of_vanishing():
    rows = airtable_share.decode_shared_view_payload(
        _payload([_row("rec1", "Acme", state="selNEW")]))
    assert rows[0]["State"] == "selNEW"


def test_payload_without_columns_raises():
    with pytest.raises(ValueError):
        airtable_share.decode_shared_view_payload({"data": {"table": {}}})


def test_accepts_a_json_string_as_well_as_a_dict():
    import json
    rows = airtable_share.decode_shared_view_payload(
        json.dumps(_payload([_row("rec1", "Acme")])))
    assert rows[0]["Company Name"] == "Acme"


# ── connector behaviour ───────────────────────────────────────────────


def _pull(connector, rows, *, days=3):
    """Run the connector over `rows`, bypassing the browser."""
    async def _fake_fetch(url, *, timeout_ms=0):
        return airtable_share.decode_shared_view_payload(_payload(rows))
    airtable_share.fetch_shared_view_rows_orig = airtable_share.fetch_shared_view_rows
    airtable_share.fetch_shared_view_rows = _fake_fetch
    try:
        async def _go():
            since = NOW - timedelta(days=days)
            return [s async for s in connector.pull(since)]
        return asyncio.run(_go())
    finally:
        airtable_share.fetch_shared_view_rows = airtable_share.fetch_shared_view_rows_orig


@pytest.fixture
def connector(tmp_path, monkeypatch):
    monkeypatch.setenv("WARN_CACHE_PATH", str(tmp_path / "warn.json"))
    monkeypatch.delenv("WARN_USE_CACHE", raising=False)
    return WarnTrackerConnector()


def test_advert_rows_never_become_signals(connector):
    signals = _pull(connector, [
        _row("rec0", "✨ Want historical data or alerts?\n👉 warntracker.com/get-data",
             notice=_iso(1)),
        _row("rec1", "Elevate Textiles, Inc.", notice=_iso(1)),
    ])
    names = [s.company_name_raw for s in signals]
    assert names == ["Elevate Textiles, Inc."]


def test_iso_instants_parse_and_land_in_window(connector):
    signals = _pull(connector, [_row("rec1", "Acme Health", notice=_iso(1))])
    assert len(signals) == 1
    assert signals[0].observed_at.date() == (NOW - timedelta(days=1)).date()


def test_windows_on_notice_date_not_layoff_date(connector):
    """The 508-row trap: an OLD filing whose layoff date is in the future must
    NOT be re-admitted — that is the whole reason we window on Notice Date."""
    old_filing_future_layoff = _row(
        "rec1", "Campbell Soup Company",
        notice="2024-05-28T00:00:00.000Z",
        layoff=(NOW + timedelta(days=60)).strftime("%Y-%m-%dT00:00:00.000Z"))
    fresh = _row("rec2", "RP Professional Services", notice=_iso(1),
                 layoff=(NOW + timedelta(days=60)).strftime("%Y-%m-%dT00:00:00.000Z"))
    signals = _pull(connector, [old_filing_future_layoff, fresh])
    assert [s.company_name_raw for s in signals] == ["RP Professional Services"]


def test_layoff_date_is_the_fallback_when_a_row_has_no_notice_date(connector):
    signals = _pull(connector, [_row("rec1", "No Notice Co", notice=None,
                                     layoff=_iso(1))])
    assert len(signals) == 1


def test_range_is_read_at_its_lower_bound(connector):
    """'0 - 10' is below MIN_LAID_OFF and must drop; '101 - 250' must pass."""
    signals = _pull(connector, [
        _row("rec1", "Tiny Co", rng="0 - 10", notice=_iso(1)),
        _row("rec2", "Big Co", rng="101 - 250", notice=_iso(1)),
    ])
    assert [s.company_name_raw for s in signals] == ["Big Co"]
    assert signals[0].payload["laid_off_count"] == 101


def test_payload_carries_both_dates_and_the_layoff_type(connector):
    layoff = (NOW + timedelta(days=60)).strftime("%Y-%m-%dT00:00:00.000Z")
    signals = _pull(connector, [_row("rec1", "Acme", notice=_iso(1),
                                     layoff=layoff)])
    p = signals[0].payload
    assert p["notice_date"] and p["layoff_date"] == layoff
    assert p["layoff_type"] == "Permanent"
    assert p["state"] == "NC"


def test_a_frozen_share_still_raises_loudly(connector):
    """The tripwire that caught the original death must survive the swap."""
    stale = _row("rec1", "Ancient Co", notice="2026-01-01T00:00:00.000Z")
    with pytest.raises(RuntimeError, match="feed stale"):
        _pull(connector, [stale])


def test_distinct_filings_on_one_day_keep_distinct_ids(connector):
    """Two plants, one company, same date — collapsing them would silently
    drop the second notice."""
    signals = _pull(connector, [
        _row("rec1", "Acme Health", state="selNC", notice=_iso(1)),
        _row("rec2", "Acme Health", state="selUT", notice=_iso(1)),
    ])
    assert len({s.source_external_id for s in signals}) == 2
