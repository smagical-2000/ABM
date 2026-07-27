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
    action, _, _nid = _classify_import_row(
        "Acme Health", "csv_acme",
        get_company=lambda k: _co("Acme Health"),     # not reached
        exists=lambda i: i == "acc_" + key)
    assert action == "skip"


def test_skip_when_already_scored_from_csv():
    action, _, _nid = _classify_import_row(
        "New Co", "csv_newco",
        get_company=lambda k: None, exists=lambda i: i == "csv_newco")
    assert action == "skip"


def test_move_a_live_discovery_company():
    key = normalize_company_name("Beta Clinic")
    company = _co("Beta Clinic", "qualified")
    action, c, _nid = _classify_import_row(
        "Beta Clinic", "csv_beta",
        get_company=lambda k: company if k == key else None, exists=lambda i: False)
    assert action == "move" and c is company


def test_needs_review_company_also_moves():
    action, _, _nid = _classify_import_row(
        "Gamma Group", "csv_gamma",
        get_company=lambda k: _co("Gamma Group", "needs_review"), exists=lambda i: False)
    assert action == "move"


def test_disqualified_discovery_is_new_not_moved():
    # A company the AI disqualified isn't yanked into Scored by an overlapping CSV.
    action, _, _nid = _classify_import_row(
        "Reject Co", "csv_reject",
        get_company=lambda k: _co("Reject Co", "disqualified"), exists=lambda i: False)
    assert action == "new"


def test_unknown_company_is_new():
    action, _, _nid = _classify_import_row(
        "Fresh Co", "csv_fresh", get_company=lambda k: None, exists=lambda i: False)
    assert action == "new"


def test_domain_conflict_mints_distinct_id_never_overwrites():
    """Review 2026-07-27: 'new' on a domain conflict must carry a DISTINCT id —
    the row's own csv_<name> id collides with the incumbent and upsert_account
    is ON CONFLICT DO UPDATE, so a same-id 'new' is an in-place hijack."""
    action, _, new_id = _classify_import_row(
        "Healthfirst", "csv_healthfirst",
        get_company=lambda k: None,
        exists=lambda aid: aid == "csv_healthfirst",
        domain="healthfirst.org",
        domain_of=lambda aid: "hf.org")
    assert action == "new"
    assert new_id and new_id != "csv_healthfirst"
    assert new_id.startswith("csv_healthfirst__")


def test_same_domain_still_skips_with_no_new_id():
    action, _, new_id = _classify_import_row(
        "Healthfirst", "csv_healthfirst",
        get_company=lambda k: None,
        exists=lambda aid: aid == "csv_healthfirst",
        domain="hf.org",
        domain_of=lambda aid: "hf.org")
    assert action == "skip" and new_id is None
