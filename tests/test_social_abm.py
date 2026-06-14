"""Social ingest: an ABM-target company is authoritative.

A decision-maker at an account already on the ABM target list, engaging with a
(competitor) post, is qualified WITHOUT the paid ICP website qualifier - the
list is the qualification. This protects the highest-value signal and saves the
qualify spend on accounts we already chose to target.
"""

import pytest

from auto_search.models import QualificationResult
from auto_search.social.ingest import ingest_engager
from auto_search.social.models import Engager


class _FakeRepo:
    def __init__(self):
        self.saved = []

    def already_qualified(self, key):
        return False

    def add_signal(self, key, sig):
        return True

    def save_candidate(self, cand):
        self.saved.append(cand)
        return cand.company_key


class _Match:
    """Duck-typed AbmMatch (only the attrs ingest reads)."""

    target_name = "Bryan Health"
    segment = "Health Systems"
    source_sheet = "Health Systems"


def _engager(company="Bryan Health"):
    return Engager(
        full_name="Jane Doe", job_title="Chief Financial Officer",
        company_name=company, company_website="bryanhealth.com",
        source="competitor_post", engagement_type="like",
        linkedin_url="https://www.linkedin.com/in/janedoe",
    )


async def _must_not_call(_signal):
    raise AssertionError("ICP qualifier must not run for an ABM-list company")


@pytest.mark.asyncio
async def test_abm_target_engager_qualifies_without_icp_call():
    repo = _FakeRepo()
    res = await ingest_engager(
        _engager(), repo=repo, qualify_fn=_must_not_call,
        abm_lookup=lambda name, domain=None: _Match() if "bryan" in name.lower() else None,
    )
    assert (res.action, res.reason) == ("qualified", "qualified")
    assert len(repo.saved) == 1
    q = repo.saved[0].qualification
    assert q.qualified is True
    assert q.decided_by == "rules"               # not an LLM verdict
    assert q.segment == "health_system"          # mapped from the "Health Systems" sheet
    assert "ABM target list" in q.reasoning


@pytest.mark.asyncio
async def test_non_abm_engager_still_uses_icp_qualifier():
    repo = _FakeRepo()
    calls = {"n": 0}

    async def fake_icp(_signal):
        calls["n"] += 1
        return QualificationResult(qualified=True, confidence=0.9, reasoning="icp says yes")

    res = await ingest_engager(
        _engager("Some Unlisted Clinic"), repo=repo, qualify_fn=fake_icp,
        abm_lookup=lambda name, domain=None: None,   # not on the list
    )
    assert calls["n"] == 1                            # fell through to the paid qualifier
    assert res.action == "qualified"
    assert repo.saved[0].qualification.decided_by == "llm"


def test_abm_segment_mapping():
    from auto_search.social.ingest import _abm_segment

    class M:
        segment = source_sheet = None

    M.segment, M.source_sheet = "Payers", None
    assert _abm_segment(M) == "payer"
    M.segment, M.source_sheet = "Physician Group - Urology", None
    assert _abm_segment(M) == "specialty"
    M.segment, M.source_sheet = None, "Independent Hospitals"
    assert _abm_segment(M) == "health_system"
    M.segment, M.source_sheet = "Sheet30", None
    assert _abm_segment(M) is None
