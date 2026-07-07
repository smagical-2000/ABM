"""Apify parsing — pinned to the real dataset shapes captured from live runs."""

from auto_search.social.apify import normalize_enrichment, normalize_profile, parse_engagers

# Real shape from harvestapi~linkedin-profile-posts: a flat list mixing
# 'reaction'/'comment'/'post' items; actors carry name/position/linkedinUrl.
_POSTS_DATASET = [
    {"type": "reaction", "postId": "7462885807754956800", "reactionType": "LIKE",
     "actor": {"name": "Solome Tibebu", "position": "Founder of Behavioral Health Tech",
               "linkedinUrl": "https://www.linkedin.com/in/ACoAAAJA_mQB"}},
    {"type": "reaction", "postId": "7462885807754956800", "reactionType": "PRAISE",
     "actor": {"name": "Geoffrey G. Martin", "position": "President @ Magical | Board Member",
               "linkedinUrl": "https://www.linkedin.com/in/ACoAAAA6vi4B"}},
    {"type": "comment", "postId": "7462885807754956800", "commentary": "Excited for this!",
     "actor": {"name": "Dana Reviewer", "position": "VP Revenue Cycle at Acme Health",
               "linkedinUrl": "https://www.linkedin.com/in/danareviewer"}},
    {"type": "post", "id": "7462885807754956800", "author": {"name": "Magical"},
     "content": "Join our session on RCM automation",
     "linkedinUrl": "https://www.linkedin.com/feed/update/urn:li:activity:7462885807754956800",
     "engagement": {"likes": 18}},
]


def test_parse_engagers_extracts_reactions_and_comments():
    engagers = parse_engagers(_POSTS_DATASET)
    assert len(engagers) == 3  # 2 reactions + 1 comment; the post itself is not an engager
    by_name = {e.name: e for e in engagers}
    solome = by_name["Solome Tibebu"]
    assert solome.position.startswith("Founder")
    assert solome.engagement_type == "like"
    assert solome.linkedin_url.endswith("ACoAAAJA_mQB")
    # parent post url/title attached for context
    assert "activity:7462885807754956800" in solome.post_url
    assert "RCM automation" in solome.post_title


def test_parse_engagers_marks_comment_type_and_text():
    dana = next(e for e in parse_engagers(_POSTS_DATASET) if e.name == "Dana Reviewer")
    assert dana.engagement_type == "comment"
    assert dana.comment_text == "Excited for this!"


def test_parse_engagers_skips_actors_without_a_name():
    items = [{"type": "reaction", "postId": "1", "actor": {"position": "VP", "name": ""}}]
    assert parse_engagers(items) == []


def test_normalize_enrichment_nested_data():
    items = [{"data": {
        "full_name": "Solome Tibebu", "job_title": "President",
        "company": "Behavioral Health Tech", "company_domain": "behavioralhealthtech.com",
        "company_industry": "Hospitals and Health Care", "company_employee_count": 31,
        "linkedin_url": "https://www.linkedin.com/in/solome/"}}]
    out = normalize_enrichment(items)
    assert out["company"] == "Behavioral Health Tech"
    assert out["company_domain"] == "behavioralhealthtech.com"      # a REAL domain
    assert out["job_title"] == "President"
    assert out["employee_count"] == 31


def test_normalize_enrichment_empty_is_none():
    assert normalize_enrichment([]) is None
    assert normalize_enrichment([{"data": {}}]) is None


def test_normalize_profile_resolves_company_and_public_slug():
    """harvestapi profile scraper result: current company from currentPosition[0], plus
    the RESOLVED public slug (this is what fixes the ACoAAA enrichment dead-end)."""
    items = [{
        "firstName": "Alejandro", "lastName": "Fernandez",
        "headline": "CEO", "linkedinUrl": "https://www.linkedin.com/in/alexfernandezmba",
        "emails": [],
        "currentPosition": [{"position": "Chief Executive Officer",
                             "companyName": "Synergy Orthopedic Specialists",
                             "companyLinkedinUrl": "https://www.linkedin.com/company/synergy/"}],
    }]
    out = normalize_profile(items)
    assert out["company"] == "Synergy Orthopedic Specialists"
    assert out["linkedin_url"] == "https://www.linkedin.com/in/alexfernandezmba"   # public slug
    assert out["job_title"] == "Chief Executive Officer"
    assert out["full_name"] == "Alejandro Fernandez"


def test_normalize_profile_falls_back_to_experience_and_handles_empty():
    items = [{"firstName": "Jo", "lastName": "Doe", "currentPosition": [],
              "experience": [{"position": "CFO", "companyName": "Acme Health"}]}]
    assert normalize_profile(items)["company"] == "Acme Health"
    assert normalize_profile([]) is None
    assert normalize_profile([{"firstName": "", "lastName": "", "currentPosition": []}]) is None


def test_normalize_enrichment_location_objects_become_strings():
    """Actor schema drift (2026-07-07): city/country arrived as objects and
    crashed the US filter — the mapping must normalize them to strings."""
    items = [{"data": {
        "full_name": "Jane Doe", "company": "Acme Health",
        "city": {"name": "Chicago"}, "country": {"name": "United States"}}}]
    out = normalize_enrichment(items)
    assert out["city"] == "Chicago"
    assert out["country"] == "United States"

    weird = [{"data": {"full_name": "Jane Doe", "company": "Acme Health",
                       "city": {"code": 1}, "country": 7}}]
    out2 = normalize_enrichment(weird)
    assert out2["city"] is None and out2["country"] is None
