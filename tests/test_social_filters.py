"""Pure gates for social ingestion: seniority, Magical-employee, event attendance.

These run before the paid qualifier, so their boundaries are pinned here.
"""

import pytest

from auto_search.social import is_attending, is_decision_maker, is_magical


@pytest.mark.parametrize("title", [
    "Chief Financial Officer", "CFO", "CEO", "COO", "CIO",
    "VP, Revenue Cycle", "Vice President of Finance", "SVP Operations",
    "Director of Patient Financial Services", "Director, Revenue Cycle",
    "Head of RCM", "Owner", "Founder & CEO", "President",
    "Managing Partner", "Principal", "Managing Director",
])
def test_decision_makers_pass(title):
    ok, _ = is_decision_maker(title)
    assert ok, title


@pytest.mark.parametrize("title", [
    "Revenue Cycle Manager", "Billing Specialist", "Medical Coder",
    "Senior Accountant", "Patient Access Representative", "RCM Analyst",
    "Assistant Director of Billing", "Associate Director, Coding",
    "Coordinator", "",
    # Title-shape false positives the classifier must exclude:
    "Assistant Vice President", "Associate Vice President, Claims",
    "Principal Software Engineer", "Principal Scientist", "Principal Investigator",
    "Associate Partner",            # associate demotes partner
])
def test_below_bar_rejected(title):
    ok, _ = is_decision_maker(title)
    assert not ok, title


def test_structured_levels_pass_even_without_title():
    ok, reason = is_decision_maker(None, job_title_levels=["director"])
    assert ok and reason == "level"


def test_manager_level_alone_is_below_bar():
    ok, _ = is_decision_maker("Operations Manager", job_title_levels=["manager"])
    assert not ok


def test_is_magical_by_name_exact():
    assert is_magical("Magical")
    assert is_magical("Magical, Inc.")
    assert not is_magical("Magical Touch Dental")     # different org, substring only


def test_is_magical_by_url_marker():
    assert is_magical("Someone Inc", "https://www.linkedin.com/company/getmagical")
    assert is_magical(None, None, "https://getmagical.com/careers")
    assert not is_magical("Acme", "https://acme.com")


@pytest.mark.parametrize("text", [
    "I'll be at HLTH next week!", "Excited to attend the summit",
    "See you there 👋", "Registered for the conference",
    "Come find us at booth 412", "Count me in",
])
def test_attendance_confirmed(text):
    ok, _ = is_attending(text)
    assert ok, text


def test_attendance_unconfirmed_without_text():
    assert is_attending(None)[0] is False
    assert is_attending("Great post, thanks for sharing")[0] is False


def test_attendance_can_match_on_post_title():
    ok, _ = is_attending(None, post_title="Join us at the Behavioral Health Tech summit — attending?")
    assert ok


@pytest.mark.parametrize("text", [
    "Sadly not attending this year", "Won't be there unfortunately",
    "Can't make it this time", "Unable to attend — next year!",
    "Going to miss this one",
])
def test_explicit_decline_is_not_attending(text):
    assert is_attending(text)[0] is False, text


@pytest.mark.parametrize("text", [
    "I’ll be there — see you at booth 12!",   # curly apostrophe (iOS/LinkedIn)
    "Can’t wait to attend",
    "I’m in!",
])
def test_attendance_handles_curly_apostrophes(text):
    assert is_attending(text)[0] is True, text
