"""Unit tests for the shared ABM annotation seam (auto_search.abm.annotate).

match_one() is the one function both the panel and the scored board call, so its
contract — no list → no-op, domain → confirmed, name+state → confirmed, name-only
→ review — is pinned here, independent of the API wiring.
"""

from auto_search.abm import match_one, states_from_locations
from auto_search.abm.matcher import AbmIndex
from auto_search.abm.models import TargetAccount
from auto_search.normalize import normalize_company_name


def _target(name, *, domain=None, state=None):
    return TargetAccount(
        name=name, keys=[normalize_company_name(name)], domain=domain, state=state,
        source_sheet="Health Systems",
    )


def _index():
    return AbmIndex([
        _target("Bryan Health", domain="bryanhealth.com", state="NE"),
        _target("Parkview Health System", state="IN"),
    ])


def test_no_index_is_a_noop():
    assert match_one(None, name="Bryan Health", domain="bryanhealth.com") is None


def test_empty_index_is_a_noop():
    assert match_one(AbmIndex([]), name="Bryan Health") is None


def test_domain_match_is_confirmed():
    m = match_one(_index(), name="Bryan Medical", domain="bryanhealth.com")
    assert m is not None
    assert (m.tier, m.how) == ("confirmed", "domain")


def test_name_plus_agreeing_state_is_confirmed():
    m = match_one(_index(), name="Bryan Health", states=["NE"])
    assert (m.tier, m.how) == ("confirmed", "name+state")


def test_name_only_is_review():
    m = match_one(_index(), name="Parkview Health System")
    assert (m.tier, m.how) == ("review", "name")


def test_name_with_disagreeing_state_is_review_not_dropped():
    # Same name, different state than the target (IN) — surfaced, never conflated.
    m = match_one(_index(), name="Parkview Health System", states=["CO"])
    assert m is not None and m.tier == "review"


def test_unknown_company_returns_none():
    assert match_one(_index(), name="Totally Unrelated Clinic") is None


def test_states_from_locations_parses_and_filters():
    locations = ["Lincoln, NE", None, "Remote", "Fort Wayne, IN", "", 123]
    assert states_from_locations(locations) == ["NE", "IN"]
