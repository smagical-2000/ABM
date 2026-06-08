"""poll_events — keyword post search → confirm attendance → US + decision-maker
→ enrich → ICP, with Apify + the qualifier mocked (pure, free).

The product rule under test: only a PERSON whose post TEXT confirms attendance,
who is a decision-maker AND US-based, is enriched + qualified — topic commentary,
the event's own page, juniors, and non-US authors are all dropped.
"""

import pytest

from auto_search.social.apify import EventPost


class FakeRepo:
    def __init__(self):
        self.saved = []
        self._rows = {}

    def already_qualified(self, key):
        return key in self._rows

    def add_signal(self, key, signal):  # noqa: ARG002
        return True

    def update_signal(self, key, signal):  # noqa: ARG002
        return True

    def get(self, key):
        return self._rows.get(key)

    def save_candidate(self, candidate):
        self.saved.append(candidate)
        self._rows[candidate.company_key] = {
            "icp_status": candidate.qualification.to_status()}
        return candidate.company_key


def _qualify(qualified=True):
    async def _fn(signal):  # noqa: ARG001
        from auto_search.models import QualificationResult
        return QualificationResult(qualified=qualified, confidence=0.9,
                                   reasoning="x", segment="health_system")
    return _fn


_POSTS = [
    # person, attended, decision-maker → the keeper (enrich returns US + company)
    EventPost(author_name="Ferry Lagarde", author_headline="CIO at Acme Health",
              author_url="https://www.linkedin.com/in/ferry", post_url="p1",
              text="Last week I attended HIMSS26 — great conversations on RCM.", keyword="HIMSS26"),
    # the event's OWN page (not a person) → dropped
    EventPost(author_name="HIMSS", author_headline="Conference",
              author_url="https://www.linkedin.com/showcase/himss/", post_url="p2",
              text="AI in healthcare is here.", keyword="HIMSS26"),
    # person + DM but only topic commentary (no attendance verb) → dropped
    EventPost(author_name="Talker", author_headline="VP Strategy",
              author_url="https://www.linkedin.com/in/talker", post_url="p3",
              text="HIMSS26 will be huge for the industry this year.", keyword="HIMSS26"),
    # person, attended, but JUNIOR → dropped before enrich
    EventPost(author_name="Junior", author_headline="Marketing Coordinator",
              author_url="https://www.linkedin.com/in/junior", post_url="p4",
              text="So happy I attended HIMSS26!", keyword="HIMSS26"),
    # person, attended, DM, but NON-US (enrich returns UK) → dropped after enrich
    EventPost(author_name="Brit Exec", author_headline="Chief Executive Officer",
              author_url="https://www.linkedin.com/in/brit", post_url="p5",
              text="Fantastic days at HIMSS26 in Copenhagen.", keyword="HIMSS26"),
]


# Enrichment returns the REAL job_title (the post headline is a tagline, so the
# decision-maker check runs on this, inside ingest, not on the headline).
_ENRICHED = {
    "ferry": {"full_name": "Ferry Lagarde", "job_title": "Chief Information Officer",
              "company": "Acme Health", "company_domain": "acme.example",
              "country": "United States", "city": "Boston, MA"},
    "junior": {"full_name": "Junior Person", "job_title": "Marketing Coordinator",
               "company": "Junior Health Co", "country": "United States"},
    "brit": {"full_name": "Brit Exec", "job_title": "CEO",
             "company": "NHS Trust", "country": "United Kingdom"},
}


def _make_enrich(calls):
    async def _enrich(url):
        calls.append(url)
        for k, v in _ENRICHED.items():
            if k in url:
                return v
        return None
    return _enrich


async def _search(keywords, **kw):  # noqa: ARG001
    return list(_POSTS)


@pytest.mark.asyncio
async def test_only_us_decision_maker_attendees_are_qualified():
    from auto_search.social.poll import poll_events
    calls = []
    repo = FakeRepo()
    summary = await poll_events(
        ["HIMSS26"], repo=repo, search_fn=_search, enrich_fn=_make_enrich(calls),
        qualify_fn=_qualify(True))

    # Every attending PERSON is enriched (ferry, junior, brit) — the org page is
    # dropped free, and topic-commentary ("will be huge") fails the attendance gate.
    assert set(calls) == {"https://www.linkedin.com/in/ferry",
                          "https://www.linkedin.com/in/junior",
                          "https://www.linkedin.com/in/brit"}
    assert summary["skipped"].get("not_a_person") == 1            # the showcase page
    assert summary["skipped"].get("attendance_unconfirmed") == 1  # "will be huge" commentary
    assert summary["skipped"].get("not_us") == 1                  # the UK CEO (post-enrich)
    # The coordinator is enriched but rejected by the decision-maker check on the
    # ENRICHED title (inside ingest) — not on the headline tagline.
    assert summary["skipped"].get("not_decision_maker") == 1
    # Exactly one qualified company: Ferry, a US CIO at a health system.
    assert summary["qualified"] == 1
    assert repo.saved and repo.saved[0].company_name == "Acme Health"


@pytest.mark.asyncio
async def test_no_keywords_is_a_noop():
    from auto_search.social.poll import poll_events
    calls = []
    summary = await poll_events([], repo=FakeRepo(), search_fn=_search,
                                enrich_fn=_make_enrich(calls), qualify_fn=_qualify(True))
    assert calls == [] and summary["posts"] == 0


def test_event_post_author_is_person():
    assert EventPost(author_name="A", author_url="https://www.linkedin.com/in/a").author_is_person
    assert not EventPost(author_name="B",
                         author_url="https://www.linkedin.com/showcase/x/").author_is_person
    assert not EventPost(author_name="C", author_url=None).author_is_person
