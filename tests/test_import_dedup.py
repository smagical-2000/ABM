"""CSV import dedup-by-identity (_classify_import_row): skip already-scored, move a
live discovery company into Scored, else create new — so an upload that overlaps
Discovery stops making signal-less twins."""
from types import SimpleNamespace

from auto_search.api.app import _classify_import_row
from auto_search.normalize import normalize_company_name


def _co(name, icp="qualified"):
    return SimpleNamespace(company_key=normalize_company_name(name), icp_status=icp)


def test_skip_when_already_scored_from_discovery():
    # An "acc_<key>" row already exists → promoted-from-discovery → skip.
    key = normalize_company_name("Acme Health")
    action, _ = _classify_import_row(
        "Acme Health", "csv_acme",
        get_company=lambda k: _co("Acme Health"),     # not reached
        exists=lambda i: i == "acc_" + key)
    assert action == "skip"


def test_skip_when_already_scored_from_csv():
    action, _ = _classify_import_row(
        "New Co", "csv_newco",
        get_company=lambda k: None, exists=lambda i: i == "csv_newco")
    assert action == "skip"


def test_move_a_live_discovery_company():
    key = normalize_company_name("Beta Clinic")
    company = _co("Beta Clinic", "qualified")
    action, c = _classify_import_row(
        "Beta Clinic", "csv_beta",
        get_company=lambda k: company if k == key else None, exists=lambda i: False)
    assert action == "move" and c is company


def test_needs_review_company_also_moves():
    action, _ = _classify_import_row(
        "Gamma Group", "csv_gamma",
        get_company=lambda k: _co("Gamma Group", "needs_review"), exists=lambda i: False)
    assert action == "move"


def test_disqualified_discovery_is_new_not_moved():
    # A company the AI disqualified isn't yanked into Scored by an overlapping CSV.
    action, _ = _classify_import_row(
        "Reject Co", "csv_reject",
        get_company=lambda k: _co("Reject Co", "disqualified"), exists=lambda i: False)
    assert action == "new"


def test_unknown_company_is_new():
    action, _ = _classify_import_row(
        "Fresh Co", "csv_fresh", get_company=lambda k: None, exists=lambda i: False)
    assert action == "new"
