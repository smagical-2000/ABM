"""Apify parsing — pinned to the real dataset shapes captured from live runs."""

from auto_search.social.apify import normalize_enrichment, parse_engagers

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
