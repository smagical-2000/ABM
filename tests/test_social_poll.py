"""poll_targets orchestration — the cost-shaped flow with Apify mocked.

The load-bearing guarantee: we filter on the free `position` headline BEFORE
paying to enrich, so a junior liker is NEVER enriched, and Magical's own staff
are dropped on the headline. Apify fetch/enrich and the LLM qualifier are all
injected, so this is pure and free.
"""

import pytest

from auto_search.social import SocialTarget
from auto_search.social.apify import RawEngager


class FakeRepo:
    """Minimal DiscoveryRepository for the poll: records saves/signals and serves
    back the icp_status (so the qualify-first → ICP-gate → enrich flow works)."""

    def __init__(self):
        self.saved = []
        self.signals = []
        self.updated = []
        self._rows = {}        # company_key -> {"icp_status": ...}
        self._dupe_keys = set()  # keys whose signal already exists → add_signal=False

    def already_qualified(self, key):
        return key in self._rows

    def add_signal(self, key, signal):
        self.signals.append((key, signal))
        return key not in self._dupe_keys   # False = already-stored (duplicate)

    def update_signal(self, key, signal):
        self.updated.append((key, signal))
        return True

    def save_candidate(self, candidate):
        self.saved.append(candidate)
        self._rows[candidate.company_key] = {
            "icp_status": candidate.qualification.to_status()}
        return candidate.company_key

    def get(self, key):
        return self._rows.get(key)


def _qualify(qualified=True):
    async def _fn(signal):  # noqa: ARG001
        from auto_search.models import QualificationResult
        return QualificationResult(
            qualified=qualified, confidence=0.9, reasoning="fit",
            segment="specialty", needs_human_review=False)
    return _fn


_fake_qualify = _qualify(True)


# Three reactors: a Founder (DM), a junior VA (skip), a President @ Magical (drop).
_RAW = [
    RawEngager(name="Solome Tibebu", position="Founder of Behavioral Health Tech",
               linkedin_url="https://www.linkedin.com/in/ACoAAAJA_mQB"),
    RawEngager(name="Mannilyn Bunao", position="Virtual Assistant",
               linkedin_url="https://www.linkedin.com/in/ACoAAESzwVkB"),
    RawEngager(name="Geoffrey Martin", position="President @ Magical | Board Member",
               linkedin_url="https://www.linkedin.com/in/ACoAAAA6vi4B"),
]


@pytest.fixture
def enrich_calls():
    return []


def _make_enrich(calls):
    async def _enrich(url):
        calls.append(url)
        return {"full_name": "Solome Tibebu", "job_title": "President",
                "company": "Behavioral Health Tech", "company_domain": "behavioralhealthtech.com"}
    return _enrich


async def _fetch(urls, **kw):   # noqa: ARG001
    return list(_RAW)


@pytest.mark.asyncio
async def test_filters_before_enriching(enrich_calls):
    from auto_search.social.poll import poll_targets
    repo = FakeRepo()
    summary = await poll_targets(
        [SocialTarget(linkedin_url="https://www.linkedin.com/company/getmagical", kind="own")],
        repo=repo, fetch_fn=_fetch, enrich_fn=_make_enrich(enrich_calls),
        qualify_fn=_fake_qualify)

    # Only the Founder is enriched — the VA (junior) and the Magical exec are
    # dropped on the free headline filter BEFORE any paid enrich call.
    assert enrich_calls == ["https://www.linkedin.com/in/ACoAAAJA_mQB"]
    assert summary["decision_makers"] == 1
    assert summary["enriched"] == 1
    assert summary["qualified"] == 1
    assert summary["skipped"].get("not_decision_maker") == 1
    assert summary["skipped"].get("magical_employee") == 1
    assert repo.saved and repo.saved[0].company_name == "Behavioral Health Tech"


@pytest.mark.asyncio
async def test_dedup_across_posts(enrich_calls):
    from auto_search.social.poll import poll_targets

    async def _fetch_dupes(urls, **kw):   # noqa: ARG001
        return [_RAW[0], _RAW[0]]         # same Founder twice

    repo = FakeRepo()
    summary = await poll_targets(
        [SocialTarget(linkedin_url="https://www.linkedin.com/company/getmagical", kind="own")],
        repo=repo, fetch_fn=_fetch_dupes, enrich_fn=_make_enrich(enrich_calls),
        qualify_fn=_fake_qualify)
    assert len(enrich_calls) == 1        # deduped by profile URL → enriched once
    assert summary["duplicates"] == 1


@pytest.mark.asyncio
async def test_enrich_cap_caps_paid_calls(enrich_calls):
    from auto_search.social.poll import poll_targets

    dms = [RawEngager(name=f"Chief {i}", position="Chief Executive Officer",
                      linkedin_url=f"https://www.linkedin.com/in/exec{i}") for i in range(5)]

    async def _fetch_dms(urls, **kw):     # noqa: ARG001
        return dms

    repo = FakeRepo()
    summary = await poll_targets(
        [SocialTarget(linkedin_url="https://www.linkedin.com/company/getmagical", kind="own")],
        repo=repo, fetch_fn=_fetch_dms, enrich_fn=_make_enrich(enrich_calls),
        qualify_fn=_fake_qualify, max_enrich=2)
    assert len(enrich_calls) == 2        # capped
    assert summary["skipped"].get("enrich_cap") == 3


@pytest.mark.asyncio
async def test_magical_headline_is_conservative(enrich_calls):
    """'at Magical' (the company) is dropped pre-enrich, but a real lead at a
    company merely NAMED 'Magical X' must NOT be — that would lose ICP leads."""
    from auto_search.social.poll import poll_targets

    raw = [
        RawEngager(name="Real Lead", position="COO at Magical Smiles Dental Group",
                   linkedin_url="https://www.linkedin.com/in/reallead"),
        RawEngager(name="Colleague", position="VP Sales @ Magical",
                   linkedin_url="https://www.linkedin.com/in/colleague"),
    ]

    async def _f(urls, **kw):  # noqa: ARG001
        return raw

    repo = FakeRepo()
    summary = await poll_targets(
        [SocialTarget(linkedin_url="https://www.linkedin.com/company/getmagical", kind="own")],
        repo=repo, fetch_fn=_f, enrich_fn=_make_enrich(enrich_calls), qualify_fn=_fake_qualify)
    # The dental-group COO is enriched + qualified; the Magical colleague is dropped.
    assert enrich_calls == ["https://www.linkedin.com/in/reallead"]
    assert summary["skipped"].get("magical_employee") == 1


@pytest.mark.asyncio
async def test_non_icp_company_is_not_enriched(enrich_calls):
    """The Outera case: a decision-maker whose headline names a NON-ICP company
    is ICP-checked FIRST and, failing it, is never enriched (no paid enrich)."""
    from auto_search.social.poll import poll_targets

    async def _f(urls, **kw):  # noqa: ARG001
        return [RawEngager(name="Partner Exec", position="Co-Founder & COO at Outera",
                           linkedin_url="https://www.linkedin.com/in/outeraexec")]

    repo = FakeRepo()
    summary = await poll_targets(
        [SocialTarget(linkedin_url="https://www.linkedin.com/company/getmagical", kind="own")],
        repo=repo, fetch_fn=_f, enrich_fn=_make_enrich(enrich_calls),
        qualify_fn=_qualify(qualified=False))   # Outera fails ICP
    assert enrich_calls == []                    # qualified-first → never enriched
    assert summary["skipped"].get("not_icp") == 1
    assert summary["enriched"] == 0


@pytest.mark.asyncio
async def test_duplicate_engagement_not_re_enriched(enrich_calls):
    """A re-seen engagement at an already-known ICP company must NOT re-enrich —
    otherwise every daily poll re-pays to enrich the entire standing book."""
    from auto_search.normalize import normalize_company_name
    from auto_search.social.poll import poll_targets

    async def _f(urls, **kw):  # noqa: ARG001
        return [RawEngager(name="CFO Person", position="CFO at Known Health System",
                           linkedin_url="https://www.linkedin.com/in/cfoperson")]

    repo = FakeRepo()
    key = normalize_company_name("Known Health System")
    repo._rows[key] = {"icp_status": "qualified"}   # company already qualified (ICP)
    repo._dupe_keys.add(key)                          # and this engagement already stored

    summary = await poll_targets(
        [SocialTarget(linkedin_url="https://www.linkedin.com/company/getmagical", kind="own")],
        repo=repo, fetch_fn=_f, enrich_fn=_make_enrich(enrich_calls), qualify_fn=_fake_qualify)
    assert enrich_calls == []                         # duplicate → not re-enriched
    assert summary["duplicates"] == 1
    assert summary["enriched"] == 0


def test_company_from_headline_parsing():
    from auto_search.social.poll import company_from_headline
    assert company_from_headline("Co-Founder & COO at Outera") == "Outera"
    assert company_from_headline("VP of Sales at Acme Health") == "Acme Health"
    assert company_from_headline("COO at Outera | Advisor") == "Outera"
    assert company_from_headline("Founder of Behavioral Health Tech") is None  # no at/@
    assert company_from_headline("Demand Generation Leader") is None


@pytest.mark.asyncio
async def test_engager_without_url_is_not_enriched(enrich_calls):
    """A decision-maker with no captured profile URL can't be enriched — skip,
    never pay for enrich(None)."""
    from auto_search.social.poll import poll_targets

    async def _f(urls, **kw):  # noqa: ARG001
        return [RawEngager(name="No URL Exec", position="Chief Executive Officer", linkedin_url=None)]

    repo = FakeRepo()
    summary = await poll_targets(
        [SocialTarget(linkedin_url="https://www.linkedin.com/company/getmagical", kind="own")],
        repo=repo, fetch_fn=_f, enrich_fn=_make_enrich(enrich_calls), qualify_fn=_fake_qualify)
    assert enrich_calls == []
    assert summary["skipped"].get("no_profile_url") == 1
