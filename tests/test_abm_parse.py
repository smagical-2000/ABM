"""ABM workbook parser - multi-sheet, varying columns, alias expansion."""

import io

import openpyxl

from auto_search.abm.parse import parse_workbook
from auto_search.normalize import normalize_company_name


def _workbook(sheets: list[tuple[str, list[list]]]) -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets:
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parses_varied_sheets_and_skips_non_lists():
    data = _workbook([
        ("Health Systems", [
            ["Hospital Name", "State", "Definitive ID"],
            ["Bryan Health", "NE", "123.0"],
            ["Overlake Medical Center (FKA Overlake Hospital)", "WA", "456"],
        ]),
        ("PGs - Urology", [
            ["Physician Group Name", "Website", "State"],
            ["Carolina Urology Partners", "carolinaurology.com/", "SC"],
        ]),
        ("Filters", [["Some Filter", "x"], ["a", "b"]]),   # no name column -> skipped
    ])
    targets = parse_workbook(data)
    by_name = {t.name: t for t in targets}

    assert len(targets) == 3                         # Filters sheet contributed nothing

    bry = by_name["Bryan Health"]
    assert bry.state == "NE"
    assert bry.segment == "Health Systems"
    assert bry.definitive_id == "123.0"
    assert bry.domain is None                        # hospital sheet has no website

    # (FKA ...) alias expanded into the key set
    ov = by_name["Overlake Medical Center"]
    assert normalize_company_name("Overlake Hospital") in ov.keys

    # PG sheet: website -> bare domain, friendly segment label
    cu = by_name["Carolina Urology Partners"]
    assert cu.domain == "carolinaurology.com"
    assert cu.segment == "Physician Group - Urology"
    assert cu.state == "SC"


def test_blank_rows_and_names_are_ignored():
    data = _workbook([
        ("Health Systems", [
            ["Hospital Name", "State"],
            ["", "NE"],            # blank name -> skipped
            [None, None],          # blank row -> skipped
            ["Real Hospital", "TX"],
        ]),
    ])
    targets = parse_workbook(data)
    assert [t.name for t in targets] == ["Real Hospital"]
