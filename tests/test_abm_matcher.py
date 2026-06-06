"""ABM matcher - strict precision (domain or name+state), aliases, review tier."""

from auto_search.abm.matcher import AbmIndex
from auto_search.abm.models import TargetAccount
from auto_search.normalize import normalize_company_name


def _t(name: str, *, aliases=None, **kw) -> TargetAccount:
    aliases = aliases or []
    keys = sorted({
        k for n in (name, *aliases) if (k := normalize_company_name(n))
    })
    return TargetAccount(name=name, aliases=aliases, keys=keys, **kw)


def test_domain_match_is_confirmed_even_with_different_name():
    idx = AbmIndex([_t("Answer Health", domain="answerhealth.com",
                       state="MI", source_sheet="PGs - RadiologyImaging")])
    m = idx.match("Answer Health Physician Network",
                  domain="https://www.answerhealth.com/careers")
    assert m is not None
    assert (m.tier, m.how) == ("confirmed", "domain")
    assert m.target_name == "Answer Health"


def test_name_plus_agreeing_state_is_confirmed():
    idx = AbmIndex([_t("Bryan Health", state="NE", source_sheet="Health Systems")])
    m = idx.match("Bryan Health", states=["NE"])
    assert m is not None
    assert (m.tier, m.how) == ("confirmed", "name+state")


def test_name_without_state_is_review():
    idx = AbmIndex([_t("Bryan Health", state="NE")])
    m = idx.match("Bryan Health", states=[])        # e.g. a remote posting
    assert m is not None
    assert (m.tier, m.how) == ("review", "name")


def test_name_with_conflicting_state_is_not_confirmed():
    # the Parkview case: list has CO, discovery found a job in IN (different org)
    idx = AbmIndex([_t("Parkview Health System", state="CO")])
    m = idx.match("Parkview Health System", states=["IN"])
    assert m is not None
    assert m.tier == "review"                       # surfaced, but never confirmed


def test_alias_matches_former_name():
    idx = AbmIndex([_t("Monument Health", aliases=["Regional Health"], state="SD")])
    m = idx.match("Regional Health", states=["SD"])
    assert m is not None
    assert m.tier == "confirmed"
    assert m.target_name == "Monument Health"


def test_no_match_returns_none():
    idx = AbmIndex([_t("Bryan Health", state="NE")])
    assert idx.match("Some Unlisted Clinic", states=["CA"]) is None


def test_domain_beats_name_and_size():
    idx = AbmIndex([_t("Acme", domain="acme.com", state="CA")])
    assert idx.size == 1
    assert idx.match("Acme", domain="acme.com").how == "domain"
