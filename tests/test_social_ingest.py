"""ingest_engager: the gauntlet + qualify-once-then-append behaviour.

Uses the JSON repo (real add_signal / save_candidate / already_qualified) and a
fake qualifier so no LLM is called and we can count how often it would be.
"""

import pytest

from auto_search.db.repository import JsonFileRepository
from auto_search.models import QualificationResult
from auto_search.normalize import normalize_company_name
from auto_search.social import Engager, ingest_engager

KEY = normalize_company_name("Mercy Health")   # the stored company_key


class _FakeQualifier:
    """Stand-in for the website qualifier; counts calls, always qualifies."""

    def __init__(self):
        self.calls = 0

    async def __call__(self, signal):
        self.calls += 1
        return QualificationResult(
            qualified=True, confidence=0.9, reasoning="fake",
            segment="health_system", domain="mercy.example", decided_by="llm")


@pytest.fixture
def repo(tmp_path):
    return JsonFileRepository(tmp_path / "discovery.json")


def _engager(**kw):
    base = dict(full_name="Jane Doe", job_title="VP Revenue Cycle",
                company_name="Mercy Health", source="magical_post",
                linkedin_url="https://www.linkedin.com/in/janedoe",
                post_url="https://www.linkedin.com/feed/update/urn:li:activity:1")
    base.update(kw)
    return Engager(**base)


async def test_decision_maker_is_qualified_and_panelled(repo):
    q = _FakeQualifier()
    res = await ingest_engager(_engager(), repo=repo, qualify_fn=q)
    assert res.accepted and res.action == "qualified"
    row = repo.get(KEY)
    assert row and row["icp_status"] == "qualified"
    assert row["signals"][0]["signal_type"] == "social_engagement"
    assert row["signals"][0]["payload"]["person_name"] == "Jane Doe"


async def test_non_decision_maker_skipped_before_llm(repo):
    q = _FakeQualifier()
    res = await ingest_engager(_engager(job_title="Billing Specialist"), repo=repo, qualify_fn=q)
    assert not res.accepted and res.reason == "not_decision_maker"
    assert q.calls == 0
    assert repo.get(KEY) is None


async def test_magical_employee_skipped(repo):
    q = _FakeQualifier()
    res = await ingest_engager(_engager(company_name="Magical"), repo=repo, qualify_fn=q)
    assert not res.accepted and res.reason == "magical_employee"
    assert q.calls == 0


async def test_second_engager_appends_without_requalifying(repo):
    q = _FakeQualifier()
    await ingest_engager(_engager(), repo=repo, qualify_fn=q)
    # A different decision-maker at the SAME company, different post.
    res = await ingest_engager(
        _engager(full_name="Bob Lee", linkedin_url="https://www.linkedin.com/in/boblee",
                 post_url="https://www.linkedin.com/feed/update/urn:li:activity:2"),
        repo=repo, qualify_fn=q)
    assert res.action == "appended"
    assert q.calls == 1                       # qualifier ran ONCE, not twice
    assert len(repo.get(KEY)["signals"]) == 2


async def test_duplicate_engagement_is_deduped(repo):
    q = _FakeQualifier()
    await ingest_engager(_engager(), repo=repo, qualify_fn=q)
    res = await ingest_engager(_engager(), repo=repo, qualify_fn=q)  # identical
    assert res.action == "duplicate"
    assert len(repo.get(KEY)["signals"]) == 1


async def test_event_requires_confirmed_attendance(repo):
    q = _FakeQualifier()
    # Decision-maker, but a bare like on an event post — attendance unconfirmed.
    res = await ingest_engager(
        _engager(source="event", event_name="Behavioral Health Tech",
                 post_url=None, comment_text=None), repo=repo, qualify_fn=q)
    assert not res.accepted and res.reason == "attendance_unconfirmed"
    assert q.calls == 0


async def test_event_attendee_qualifies(repo):
    q = _FakeQualifier()
    res = await ingest_engager(
        _engager(source="event", event_name="Behavioral Health Tech",
                 comment_text="Excited to attend — see you there!"),
        repo=repo, qualify_fn=q)
    assert res.accepted and res.action == "qualified"
    assert repo.get(KEY)["signals"][0]["signal_type"] == "event_attendance"


async def test_missing_company_skipped(repo):
    q = _FakeQualifier()
    res = await ingest_engager(_engager(company_name=None), repo=repo, qualify_fn=q)
    assert not res.accepted and res.reason == "no_company"
    assert q.calls == 0
