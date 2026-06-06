"""ABM util helpers - domain, state, alias parsing."""

from auto_search.abm.util import (
    bare_domain,
    extract_state,
    segment_for_sheet,
    split_aliases,
)


def test_bare_domain():
    assert bare_domain("https://www.Acme.com/careers") == "acme.com"
    assert bare_domain("answerhealth.com/") == "answerhealth.com"
    assert bare_domain("www.mbbradiology.com/") == "mbbradiology.com"
    assert bare_domain("") is None
    assert bare_domain("n/a") is None          # no dot
    assert bare_domain("see our website") is None  # space


def test_split_aliases_expands_fka_aka():
    p, a = split_aliases("Overlake Medical Center & Clinics (FKA Overlake Hospital Medical Center)")
    assert p == "Overlake Medical Center & Clinics"
    assert a == ["Overlake Hospital Medical Center"]

    p, a = split_aliases("Monument Health (FKA Regional Health)")
    assert p == "Monument Health"
    assert a == ["Regional Health"]


def test_split_aliases_drops_non_alias_parentheticals():
    # a percentage note is not an alias - dropped from the primary, no alias added
    p, a = split_aliases("Baptist Hospital (12%)")
    assert p == "Baptist Hospital"
    assert a == []

    p, a = split_aliases("Plain Name")
    assert p == "Plain Name"
    assert a == []


def test_extract_state():
    assert extract_state("NE") == "NE"
    assert extract_state("Lincoln, NE") == "NE"
    assert extract_state("Hackensack, NJ") == "NJ"
    assert extract_state("Remote") is None     # no state
    assert extract_state("Texas") is None       # full name, not a code
    assert extract_state("") is None


def test_segment_for_sheet():
    assert segment_for_sheet("PGs - Urology") == "Physician Group - Urology"
    assert segment_for_sheet("Health Systems") == "Health Systems"
