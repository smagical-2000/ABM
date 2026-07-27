"""Social ICP gate (2026-07-27): stop paying the LLM to disqualify vendors.

July's numbers: competitor-post engagement surfaced 53 companies and ONE
qualified (1.9%). The other 52 were other vendors, consultants and job seekers
who liked a competitor's post — each one paid for by the qualifier. The jobs
connector has always applied this check upstream; the social path never did.

The gate must fail OPEN (unknown industry still gets qualified) and must never
touch ABM targets, which are authoritative on their own.
"""

from __future__ import annotations

import pytest

from auto_search.models import QualificationResult
from auto_search.social.ingest import ingest_engager
from auto_search.social.models import Engager


class FakeRepo:
    def __init__(self):
        self.saved = []
        self.signals = []

    def already_qualified(self, key):
        return False

    def add_signal(self, key, signal):
        self.signals.append((key, signal))
        return True

    def save_candidate(self, candidate):
        self.saved.append(candidate)


def _engager(**kw):
    base = dict(
        full_name="Dana Reed",
        job_title="VP Revenue Cycle",
        job_title_levels=["vp"],
        company_name="Acme Health System",
        company_website="acmehealth.org",
        linkedin_url="https://linkedin.com/in/dana",
        source="competitor_post",
        engagement_type="comment",
        comment_text="Great post",
        post_title="RCM automation",
    )
    base.update(kw)
    return Engager(**base)


async def _run(engager, repo=None, **kw):
    calls = []

    async def _qualify(signal):
        calls.append(signal)
        return QualificationResult(
            qualified=True, segment="health_system", company_type="provider",
            confidence=0.9, reasoning="test", decided_by="llm")

    result = await ingest_engager(engager, repo=repo or FakeRepo(),
                                  qualify_fn=_qualify, **kw)
    return result, calls


@pytest.mark.asyncio
async def test_known_non_provider_industry_never_reaches_the_qualifier():
    """The 52-a-month case: a vendor's VP liking a competitor post."""
    result, calls = await _run(_engager(company_name="Acme Software",
                                        industry="Computer Software"))
    assert result.accepted is False
    assert result.reason == "not_healthcare_provider"
    assert calls == []          # no paid qualification


@pytest.mark.asyncio
async def test_provider_industry_still_qualifies():
    result, calls = await _run(_engager(industry="Hospitals and Health Care"))
    assert result.accepted is True
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_unknown_industry_fails_OPEN_and_still_pays():
    """A thin enrichment must never silently lose a real provider."""
    for industry in (None, "", "   "):
        result, calls = await _run(_engager(industry=industry))
        assert result.accepted is True, f"industry={industry!r} was dropped"
        assert len(calls) == 1


@pytest.mark.asyncio
async def test_abm_target_is_never_gated_out_by_industry():
    """The ABM list IS the qualification — even a weird industry label on the
    enrichment must not suppress a company we deliberately target."""
    repo = FakeRepo()
    result, calls = await _run(
        _engager(company_name="Kaiser Permanente", industry="Computer Software"),
        repo=repo,
        abm_lookup=lambda name, site: {"name": "Kaiser Permanente",
                                       "segment": "health_system"})
    assert result.accepted is True
    assert result.reason == "qualified"
    assert calls == []          # ABM path never pays the qualifier
    assert repo.saved


@pytest.mark.asyncio
async def test_already_qualified_company_is_unaffected():
    """Append-only path runs before the gate: an existing company keeps
    collecting signals even if its stored industry looks non-provider."""
    class Existing(FakeRepo):
        def already_qualified(self, key):
            return True

    repo = Existing()
    result, calls = await _run(_engager(industry="Computer Software"), repo=repo)
    assert result.accepted is True
    assert result.action == "appended"
    assert calls == []


@pytest.mark.asyncio
async def test_excluded_healthcare_adjacent_industries_are_dropped():
    """Pharma/device/vet are healthcare-ADJACENT but out of ICP — the same
    exclusions the jobs connector applies."""
    for industry in ("Pharmaceutical Manufacturing", "Medical Equipment Manufacturing",
                     "Veterinary Services"):
        result, calls = await _run(_engager(industry=industry))
        assert result.reason == "not_healthcare_provider", industry
        assert calls == []


@pytest.mark.asyncio
async def test_competitor_staff_never_becomes_a_lead():
    """2026-07-27 DM probe: Assort Health's own founders engaged with their
    team's posts and would have entered the funnel; Tennr itself once did."""
    comps = frozenset({"assorthealth", "tennr", "linkedin.com/company/tennrai"})
    result, calls = await _run(
        _engager(company_name="Assort Health", industry="Hospitals and Health Care"),
        competitor_names=comps)
    assert result.accepted is False and result.reason == "competitor_staff"
    assert calls == []
    # a real provider with the same industry sails through
    ok, calls2 = await _run(
        _engager(company_name="Acme Health System", industry="Hospitals and Health Care"),
        competitor_names=comps)
    assert ok.accepted is True and len(calls2) == 1


def test_competitor_name_set_builds_from_targets():
    from types import SimpleNamespace as T

    from auto_search.social.poll import _competitor_name_set
    s = _competitor_name_set([
        T(kind="competitor", label="Assort Health",
          linkedin_url="https://www.linkedin.com/company/assorthealth/"),
        T(kind="own", label="Magical", linkedin_url="https://www.linkedin.com/company/getmagical"),
    ])
    assert "assorthealth" in s and "linkedin.com/company/assorthealth" in s
    assert not any("getmagical" in x for x in s)


def test_tally_counts_only_passing_verdicts_as_qualified():
    """2026-07-27: qualified=16 reported while prod had 25/25 disqualified —
    action='qualified' only means the qualifier RAN. A rejection must land in
    not_icp, never in the qualified count."""
    from types import SimpleNamespace as R

    from auto_search.social.poll import _new_summary, _tally_result
    s = _new_summary()
    _tally_result(s, R(action="qualified", reason="disqualified", accepted=True))
    _tally_result(s, R(action="qualified", reason="qualified", accepted=True))
    _tally_result(s, R(action="qualified", reason="needs_review", accepted=True))
    assert s["qualified"] == 2
    assert s["skipped"].get("not_icp") == 1


def test_competitor_staff_prefix_catches_silna():
    """Employer 'Silna' vs tracked label 'Silna Health' — the exact miss that
    let a competitor qualify as a lead on 2026-07-27."""
    from auto_search.social.filters import is_competitor_staff
    comps = frozenset({"silnahealth", "linkedin.com/company/silna-health"})
    assert is_competitor_staff("Silna", competitors=comps)
    assert is_competitor_staff("Silna Health Inc", competitors=comps)
    assert not is_competitor_staff("Silvermine Health", competitors=comps)
    assert not is_competitor_staff("Sil", competitors=comps)   # <5 chars never prefix-matches


def test_investor_headlines_skip_before_spend():
    from auto_search.social.poll import _looks_investor
    assert _looks_investor("Partner, Healthcare VC at Meridian Street Capital") is False  # HC hint wins
    assert _looks_investor("Managing Partner at Stage 2 Capital")
    assert _looks_investor("Angel investor & advisor")
    assert _looks_investor("EVP & CIO, Cambia Health Solutions") is False
    assert _looks_investor("Founder, co-CEO @ Assort Health | AI Agents") is False
