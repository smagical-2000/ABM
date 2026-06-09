"""engager_from_trigify: tolerate Trigify's enrichment shape quirks.

The enrichment result can arrive nested ({data:{prospect:{...}}} or
{prospect:{...}}), as a JSON string, snake_case or camelCase — and the field
extraction must survive all of those without the workflow author hand-mapping
brittle refs.
"""

import json

from auto_search.social import engager_from_trigify

# The real shape from POST /v1/profile/enrich (verified live).
_PROSPECT = {
    "full_name": "Jane Doe", "first_name": "Jane", "last_name": "Doe",
    "job_title": "VP Revenue Cycle", "job_company_name": "Mercy Health",
    "job_company_website": "https://mercy.example", "linkedin_url": "linkedin.com/in/janedoe",
    "job_title_levels": ["vp"], "industry": "Hospital & Health Care",
}


def _ctx(**kw):
    base = {"source": "magical_post", "engagement_type": "like",
            "post_url": "https://www.linkedin.com/feed/update/urn:li:activity:1"}
    base.update(kw)
    return base


def test_nested_data_prospect():
    e = engager_from_trigify(_ctx(enrichment={"data": {"prospect": _PROSPECT}}))
    assert e.full_name == "Jane Doe"
    assert e.job_title == "VP Revenue Cycle"
    assert e.company_name == "Mercy Health"
    assert e.company_website == "https://mercy.example"
    assert e.linkedin_url == "linkedin.com/in/janedoe"
    assert e.source == "magical_post"


def test_prospect_without_data_wrapper():
    e = engager_from_trigify(_ctx(enrichment={"prospect": _PROSPECT}))
    assert e.company_name == "Mercy Health"


def test_enrichment_as_json_string():
    e = engager_from_trigify(_ctx(enrichment=json.dumps({"data": {"prospect": _PROSPECT}})))
    assert e.full_name == "Jane Doe" and e.company_name == "Mercy Health"


def test_flat_payload_without_enrichment():
    e = engager_from_trigify(_ctx(full_name="Bob Lee", job_title="CFO",
                                  company_name="Bryan Health"))
    assert e.full_name == "Bob Lee" and e.company_name == "Bryan Health"


def test_camelcase_aliases():
    e = engager_from_trigify(_ctx(enrichment={"prospect": {
        "fullName": "Cam Smith", "jobTitle": "Director", "companyName": "Centra"}}))
    assert e.full_name == "Cam Smith" and e.job_title == "Director"
    assert e.company_name == "Centra"


def test_full_name_falls_back_to_first_last():
    e = engager_from_trigify(_ctx(enrichment={"prospect": {
        "first_name": "Sara", "last_name": "Kim", "job_company_name": "X"}}))
    assert e.full_name == "Sara Kim"


def test_invalid_source_defaults_to_magical_post():
    # The user's broken config sent source:"comment"/"linkedin" — must not crash,
    # and must not silently bypass the event attendance gate.
    assert engager_from_trigify(_ctx(source="comment")).source == "magical_post"
    assert engager_from_trigify(_ctx(source="linkedin")).source == "magical_post"


def test_event_source_preserved():
    e = engager_from_trigify(_ctx(source="event", event_name="HLTH 2026"))
    assert e.source == "event" and e.event_name == "HLTH 2026"


def test_job_title_levels_string_is_coerced():
    e = engager_from_trigify(_ctx(enrichment={"prospect": {
        "full_name": "L", "job_company_name": "X", "job_title_levels": "vp,director"}}))
    assert e.job_title_levels == ["vp", "director"]
