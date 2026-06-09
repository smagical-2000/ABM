"""Tests for the Indeed job-postings connector — no live API calls.

A fake client returns canned IndeedJobs so the connector's role bucketing,
dedup, RCM-title gate, date window and domain resolution are tested
deterministically and for free.
"""

from datetime import UTC, datetime, timedelta

import pytest

from auto_search.clients.apify_jobs import IndeedJob, LinkedInJob
from auto_search.connectors.job_postings import (
    CORE,
    ESSENTIAL_RCM_TITLES,
    STANDARD,
    EssentialTitle,
    JobPostingsConnector,
    _domain_from_url,
    _indeed_domain,
    _job_to_signal,
    _linkedin_to_signal,
    _looks_rcm,
    _since_to_from_days,
    _split_loc,
    select_titles,
)

SINCE = datetime(2026, 6, 1, tzinfo=UTC)


class TestSelectTitles:
    def test_empty_returns_all(self):
        assert select_titles("") == ESSENTIAL_RCM_TITLES
        assert select_titles(None) == ESSENTIAL_RCM_TITLES

    def test_restricts_to_named_roles(self):
        sel = select_titles("revenue cycle, medical coder, medical biller")
        buckets = {t[1] for t in sel}
        assert buckets == {"Revenue Cycle", "Coder", "Biller"}

    def test_unmatched_falls_back_to_all(self):
        # Never silently search zero titles.
        assert select_titles("nonsense-role") == ESSENTIAL_RCM_TITLES


def _job(**over) -> IndeedJob:
    base = dict(
        jobKey="ik-1",
        title="Certified Medical Coder",
        companyName="Vitruvian Health",
        companyLinks={"corporateWebsite": "https://www.vitruvianhealth.com/careers"},
        location={"city": "Cleveland", "countryCode": "US",
                  "formattedAddressShort": "Cleveland, TN"},
        datePublished="2026-06-02",
        age="5 hours ago",
        jobType=["Full-time"],
        jobUrl="https://www.indeed.com/viewjob?jk=ik-1",
        isRemote="False",
    )
    base.update(over)
    return IndeedJob(**base)


class FakeClient:
    def __init__(self, by_query):
        self.by_query = by_query
        self.calls = []

    async def search_indeed(self, query, **kwargs):
        self.calls.append((query, kwargs))
        return list(self.by_query.get(query, []))


# ── _job_to_signal ────────────────────────────────────────────────────


class TestJobToSignal:
    def test_clean_coder_maps(self):
        sig, reason = _job_to_signal(_job(), "Coder", 0.78, STANDARD, SINCE, "ik-1")
        assert sig is not None, reason
        assert sig.signal_type == "job_posting"
        assert sig.source == "indeed"
        assert sig.company_name_raw == "Vitruvian Health"
        assert sig.company_domain_raw == "vitruvianhealth.com"
        assert sig.payload["role"] == "Coder"
        assert sig.payload["location"] == "Cleveland, TN"
        assert sig.payload["state"] == "TN"
        assert sig.signal_strength == 0.78
        assert sig.summary == "Hiring: Certified Medical Coder — Cleveland, TN"

    def test_missing_company_dropped(self):
        sig, reason = _job_to_signal(_job(companyName=""), "Coder", 0.78, STANDARD, SINCE, "x")
        assert sig is None and reason == "missing_company"

    def test_off_topic_title_dropped(self):
        sig, reason = _job_to_signal(
            _job(title="Staff Accountant"), "Coder", 0.78, STANDARD, SINCE, "x")
        assert sig is None and reason == "not_rcm_title"

    def test_before_window_dropped(self):
        sig, reason = _job_to_signal(
            _job(datePublished="2026-05-20"), "Coder", 0.78, STANDARD, SINCE, "x")
        assert sig is None and reason == "before_window"

    def test_same_day_kept_despite_date_granularity(self):
        # Indeed dates have no clock; a posting dated == since.date() must pass.
        sig, reason = _job_to_signal(
            _job(datePublished="2026-06-01"), "Coder", 0.78, STANDARD, SINCE, "x")
        assert sig is not None, reason

    def test_non_us_dropped(self):
        sig, reason = _job_to_signal(
            _job(location={"countryCode": "CA", "city": "Toronto"}),
            "Coder", 0.78, STANDARD, SINCE, "x")
        assert sig is None and reason == "non_us"

    def test_domain_falls_back_to_email(self):
        sig, _ = _job_to_signal(
            _job(companyLinks=None, emails=["careers@acmehealth.com"]),
            "Biller", 0.8, STANDARD, SINCE, "x")
        assert sig.company_domain_raw == "acmehealth.com"

    def test_job_board_email_domain_rejected(self):
        sig, _ = _job_to_signal(
            _job(companyLinks=None, emails=["hr@acme.jobs"]),
            "Biller", 0.8, STANDARD, SINCE, "x")
        assert sig.company_domain_raw is None


class TestTiers:
    def test_every_title_has_a_known_tier(self):
        assert all(t.tier in (CORE, STANDARD) for t in ESSENTIAL_RCM_TITLES)
        assert any(t.tier == CORE for t in ESSENTIAL_RCM_TITLES)
        assert any(t.tier == STANDARD for t in ESSENTIAL_RCM_TITLES)

    def test_signal_payload_carries_tier(self):
        # The stacking gate reads payload["tier"], so every posting must carry it.
        core, _ = _job_to_signal(_job(), "Prior Auth", 0.88, CORE, SINCE, "ik-9")
        assert core.payload["tier"] == CORE
        std, _ = _linkedin_to_signal(_lijob(), "Coder", 0.72, STANDARD, SINCE, "li-9")
        assert std.payload["tier"] == STANDARD


# ── small helpers ─────────────────────────────────────────────────────


class TestHelpers:
    def test_domain_from_url_strips_scheme_www_path(self):
        assert _domain_from_url("https://www.heart.org/careers") == "heart.org"
        assert _domain_from_url("http://acme.com") == "acme.com"
        assert _domain_from_url(None) is None
        assert _domain_from_url("not a url") is None

    def test_domain_from_url_strips_ats_subdomains(self):
        assert _domain_from_url("https://jobs.clevelandclinic.org") == "clevelandclinic.org"
        assert _domain_from_url("https://careers.chop.edu/x") == "chop.edu"
        assert _domain_from_url("https://recruiting.acme.co.uk") == "acme.co.uk"

    def test_indeed_domain_prefers_corporate_site(self):
        job = _job(companyLinks={"corporateWebsite": "http://www.foo.org"},
                   emails=["x@bar.com"])
        assert _indeed_domain(job) == "foo.org"

    def test_looks_rcm(self):
        assert _looks_rcm("Inpatient Coder")
        assert _looks_rcm("Prior Authorization Specialist")
        assert not _looks_rcm("Senior Software Engineer")

    def test_since_to_from_days(self):
        now = datetime.now(UTC)
        assert _since_to_from_days(now - timedelta(hours=12)) == "1"
        assert _since_to_from_days(now - timedelta(days=3)) == "3"
        assert _since_to_from_days(now - timedelta(days=6)) == "7"
        assert _since_to_from_days(now - timedelta(days=20)) == "14"


# ── connector: bucketing + dedup ──────────────────────────────────────


@pytest.mark.asyncio
async def test_connector_buckets_and_dedups_across_titles():
    coder = _job(jobKey="ik-1", title="Medical Coder", companyName="Acme Health")
    biller = _job(jobKey="ik-2", title="Medical Biller", companyName="Acme Health")
    fake = FakeClient({
        '"medical coder"': [coder],
        '"medical biller"': [biller, coder],   # coder re-appears under biller search
    })
    titles = [EssentialTitle('"medical coder"', "Coder", 0.78, STANDARD),
              EssentialTitle('"medical biller"', "Biller", 0.80, STANDARD)]
    conn = JobPostingsConnector(client=fake, titles=titles, max_rows=5)

    out = [s async for s in conn.pull(since=SINCE)]
    by_id = {s.source_external_id: s for s in out}

    assert set(by_id) == {"ik-1", "ik-2"}            # coder deduped, not doubled
    assert by_id["ik-1"].payload["role"] == "Coder"  # first query that saw it wins
    assert by_id["ik-2"].payload["role"] == "Biller"
    # both postings are about the same company → one dedup key downstream
    assert by_id["ik-1"].company_key == by_id["ik-2"].company_key
    assert all(c[1]["from_days"] in ("1", "3", "7", "14") for c in fake.calls)


@pytest.mark.asyncio
async def test_connector_volume_is_count_of_signals():
    # "3 Coder jobs" = three job_posting signals at one company.
    jobs = [_job(jobKey=f"k{i}", title="Medical Coder", companyName="Beacon Health")
            for i in range(3)]
    fake = FakeClient({'"medical coder"': jobs})
    conn = JobPostingsConnector(
        client=fake, titles=[EssentialTitle('"medical coder"', "Coder", 0.78, STANDARD)],
        max_rows=10)

    out = [s async for s in conn.pull(since=SINCE)]
    assert len(out) == 3
    assert {s.company_key for s in out} == {out[0].company_key}
    assert all(s.payload["role"] == "Coder" for s in out)


# ── LinkedIn mapping + cross-board merge ──────────────────────────────


def _lijob(**over) -> LinkedInJob:
    base = dict(
        id="li-1",
        title="Medical Coder",
        companyName="Acme Health",
        url="https://www.linkedin.com/jobs/view/li-1",
        location="Dallas, TX",
        postedDate="2026-06-02",
        postedTimeAgo="1 day ago",
        applicationsCount="12 applicants",
        description="Hands-on inpatient coding role.",
    )
    base.update(over)
    return LinkedInJob(**base)


class TestLinkedIn:
    def test_split_loc(self):
        assert _split_loc("Dallas, TX") == ("Dallas", "TX")
        assert _split_loc("Remote") == ("Remote", None)
        assert _split_loc("") == (None, None)
        assert _split_loc("New York, NY 10001") == ("New York", "NY")

    def test_linkedin_maps(self):
        sig, reason = _linkedin_to_signal(_lijob(), "Coder", 0.78, STANDARD, SINCE, "li-1")
        assert sig is not None, reason
        assert sig.source == "linkedin"
        assert sig.signal_type == "job_posting"
        assert sig.company_name_raw == "Acme Health"
        assert sig.payload["city"] == "Dallas"
        assert sig.payload["state"] == "TX"
        assert sig.payload["source_board"] == "linkedin"
        assert sig.payload["description"] == "Hands-on inpatient coding role."

    def test_linkedin_off_topic_dropped(self):
        sig, reason = _linkedin_to_signal(
            _lijob(title="Software Engineer"), "Coder", 0.78, STANDARD, SINCE, "x")
        assert sig is None and reason == "not_rcm_title"


class FakeBothClient:
    def __init__(self, indeed_by_q, linkedin_by_q):
        self.indeed_by_q = indeed_by_q
        self.linkedin_by_q = linkedin_by_q

    async def search_indeed(self, query, **kwargs):
        return list(self.indeed_by_q.get(query, []))

    async def search_linkedin(self, title, **kwargs):
        return list(self.linkedin_by_q.get(title, []))


@pytest.mark.asyncio
async def test_cross_board_dedup_keeps_unique_drops_crosslisted():
    # Same coder req at Acme appears on BOTH boards → counted once.
    # A different LinkedIn-only company adds yield → kept.
    indeed_coder = _job(jobKey="ik-1", title="Medical Coder", companyName="Acme Health",
                        location={"city": "Dallas", "countryCode": "US",
                                  "formattedAddressShort": "Dallas, TX"})
    li_same = _lijob(id="li-1", title="Medical Coder", companyName="Acme Health",
                     location="Dallas, TX")
    li_other = _lijob(id="li-2", title="Medical Coder", companyName="Beacon Clinic",
                      location="Austin, TX")
    fake = FakeBothClient(
        indeed_by_q={'"medical coder"': [indeed_coder]},
        linkedin_by_q={"medical coder": [li_same, li_other]},
    )
    # CORE title → searched on BOTH boards, so cross-board dedup is exercised.
    conn = JobPostingsConnector(
        client=fake, titles=[EssentialTitle('"medical coder"', "Coder", 0.78, CORE)],
        max_rows=5)

    out = [s async for s in conn.pull(since=SINCE)]
    companies = {s.company_key for s in out}
    # Acme counted once (cross-listed), Beacon added from LinkedIn.
    assert len(out) == 2
    assert len(companies) == 2
    boards = {s.payload["source_board"] for s in out}
    assert boards == {"indeed", "linkedin"}


@pytest.mark.asyncio
async def test_standard_titles_skip_linkedin_to_save_credits():
    """Scrape-cost lever: a STANDARD role searches Indeed ONLY, so a
    LinkedIn-only posting is never fetched (no second-board credit spend)."""
    indeed_hit = _job(jobKey="ik-1", title="Medical Coder", companyName="Acme Health",
                      location={"city": "Dallas", "countryCode": "US",
                                "formattedAddressShort": "Dallas, TX"})
    li_only = _lijob(id="li-9", title="Medical Coder", companyName="LinkedinOnly Health")
    fake = FakeBothClient(indeed_by_q={'"medical coder"': [indeed_hit]},
                          linkedin_by_q={"medical coder": [li_only]})
    conn = JobPostingsConnector(
        client=fake, titles=[EssentialTitle('"medical coder"', "Coder", 0.72, STANDARD)],
        max_rows=5)

    out = [s async for s in conn.pull(since=SINCE)]
    assert {s.payload["source_board"] for s in out} == {"indeed"}   # linkedin skipped
    assert all(s.company_name_raw != "LinkedinOnly Health" for s in out)


@pytest.mark.asyncio
async def test_core_titles_search_both_boards():
    """CORE roles keep both boards for max recall on the high-intent signals."""
    indeed_hit = _job(jobKey="ik-2", title="Denials Specialist", companyName="Acme Health",
                      location={"city": "Dallas", "countryCode": "US",
                                "formattedAddressShort": "Dallas, TX"})
    li_hit = _lijob(id="li-2", title="Denials Specialist", companyName="Beacon Clinic",
                    location="Austin, TX")
    fake = FakeBothClient(indeed_by_q={'"denials specialist"': [indeed_hit]},
                          linkedin_by_q={"denials specialist": [li_hit]})
    conn = JobPostingsConnector(
        client=fake, titles=[EssentialTitle('"denials specialist"', "Denials", 0.86, CORE)],
        max_rows=5)

    out = [s async for s in conn.pull(since=SINCE)]
    assert {s.payload["source_board"] for s in out} == {"indeed", "linkedin"}
