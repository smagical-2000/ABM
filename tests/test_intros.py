"""Warm intros — pure path matching, profile normalizers, service orchestration."""

import pytest

from auto_search.db.repository import JsonFileRepository
from auto_search.db.scoring_repository import ScoringJsonRepository
from auto_search.intros import paths, profiles, service
from auto_search.intros.models import FounderProfile, Stint, WarmContact
from auto_search.scoring.models import Account


def _stint(org, start=None, end=None, school=False):
    return Stint(org=org, norm=paths.norm_school(org) if school else paths.norm_company(org),
                 start_year=start, end_year=end or 9999)


def _founder(name="Geoffrey", exp=(), edu=()):
    return FounderProfile(name=name, linkedin_url="https://x/in/g",
                          experiences=list(exp), educations=list(edu))


# ── paths ──────────────────────────────────────────────────────────────


def test_shared_employer_with_overlap_outranks_different_eras():
    f = _founder(exp=[_stint("Olive", 2019, 2022)])
    c = WarmContact(name="Pat Doe")
    overlap = paths.founder_paths(f, c, [_stint("Olive Inc", 2021, 2023)], [])
    eras = paths.founder_paths(f, c, [_stint("Olive", 2010, 2012)], [])
    assert overlap[0].strength > eras[0].strength
    assert "overlapping 2021-2022" in overlap[0].evidence
    assert "different periods" in eras[0].evidence


def test_school_matching_is_conservative_full_string():
    f = _founder(edu=[_stint("University of Michigan", 2000, 2004, school=True)])
    c = WarmContact(name="Pat Doe")
    same = paths.founder_paths(f, c, [], [_stint("University of Michigan", 2002, 2006, school=True)])
    different = paths.founder_paths(f, c, [], [_stint("Michigan State University", 2002, 2006, school=True)])
    assert len(same) == 1 and same[0].kind == "shared_school"
    assert different == []                       # never conflate different schools


def test_engaged_path_matches_profile_slug_and_outranks_everything():
    c = WarmContact(name="Pat Doe", linkedin_url="https://www.linkedin.com/in/pat-doe-123/")
    ep = paths.engaged_path(c, {"pat-doe-123"}, set())
    assert ep is not None and ep.strength == paths.STRENGTH["engaged"]
    assert paths.engaged_path(WarmContact(name="Other"), {"pat-doe-123"}, set()) is None


def test_rank_orders_warmest_first():
    cold = WarmContact(name="Cold")
    warm = WarmContact(name="Warm", paths=[
        paths.WarmPath(kind="shared_school", founder="G", evidence="e", strength=40)])
    assert [c.name for c in paths.rank([cold, warm])] == ["Warm", "Cold"]


# ── normalizers (fixtures mirror the live dry-run shapes) ──────────────


def test_founder_normalizer_freshdata_shape():
    raw = {"full_name": "Geoffrey Martin", "headline": "GTM",
           "experiences": [
               {"company": "Olive", "title": "VP", "start_year": 2019,
                "end_year": "", "is_current": False},
               {"company": "Magical", "title": "GTM", "start_year": 2023,
                "end_year": "", "is_current": True}],
           "educations": [
               {"school": "University of Waterloo", "degree": "BASc",
                "start_year": 2004, "end_year": 2009}]}
    exp, edu = profiles._founder_stints(raw)
    assert exp[0].org == "Olive" and exp[0].end_year == 9999   # no end year -> open
    assert exp[1].end_year == 9999                              # is_current -> open
    assert edu[0].norm == "university of waterloo" and edu[0].end_year == 2009


def test_contact_parser_harvest_full_shape():
    item = {"firstName": "Candice", "lastName": "Heaberlin",
            "headline": "Director of Revenue Cycle at TriHealth",
            "linkedinUrl": "https://www.linkedin.com/in/abc",
            "location": {"linkedinText": "Cincinnati, Ohio"},
            "experience": [{"position": "Director", "companyName": "TriHealth",
                            "startDate": {"year": 2018}, "endDate": {"text": "Present"}}],
            "education": [{"schoolName": "Xavier University",
                           "startDate": {"year": 1999}, "endDate": {"year": 2003}}]}
    contact, exp, edu = profiles.parse_contact(item)
    assert contact.name == "Candice Heaberlin"
    assert exp[0].end_year == 9999 and exp[0].norm == "trihealth"
    assert edu[0].org == "Xavier University"
    assert profiles.parse_contact({"noName": True}) is None


# ── service orchestration ──────────────────────────────────────────────


class _FakeDiscoRepo:
    def __init__(self):
        self.profiles = []
        self.company = None

    def founder_profiles(self):
        return self.profiles

    def replace_founder_profiles(self, p):
        self.profiles = p
        return len(p)

    def get(self, key):
        return self.company


@pytest.mark.asyncio
async def test_generate_filters_sub_director_and_ranks_engaged(monkeypatch):
    repo = _FakeDiscoRepo()
    repo.company = {"signals": [{
        "signal_type": "social_engagement",
        "payload": {"person_profile_url": "https://www.linkedin.com/in/warm-exec",
                    "person_name": "Warm Exec"}}]}

    async def fake_founder(url):
        return _founder(exp=[_stint("Olive", 2019, 2022)])

    async def fake_search(company, limit=None):
        return [
            {"firstName": "Warm", "lastName": "Exec", "headline": "Chief Financial Officer",
             "linkedinUrl": "https://www.linkedin.com/in/warm-exec",
             "experience": [], "education": []},
            {"firstName": "Olive", "lastName": "Alum", "headline": "VP Revenue Cycle",
             "linkedinUrl": "https://www.linkedin.com/in/olive-alum",
             "experience": [{"position": "Lead", "companyName": "Olive",
                             "startDate": {"year": 2020}, "endDate": {"year": 2021}}],
             "education": []},
            {"firstName": "Too", "lastName": "Junior", "headline": "Revenue Cycle Supervisor",
             "linkedinUrl": "https://www.linkedin.com/in/junior",
             "experience": [], "education": []},
        ]

    async def no_apollo(domain):
        return []                                        # force the Apify fallback path

    monkeypatch.setattr(service.profiles, "fetch_founder", fake_founder)
    monkeypatch.setattr(service.profiles, "apollo_contacts", no_apollo)
    monkeypatch.setattr(service.profiles, "search_contacts", fake_search)

    costs = []
    account = {"name": "TriHealth", "discovery_company_key": "trihealth"}
    out = await service.generate(account, discovery_repo=repo,
                                 on_cost=lambda usd, step: costs.append(step))

    assert out["state"] == "ready" and out["source"] == "apify"
    names = [c["name"] for c in out["contacts"]]
    assert "Too Junior" not in names                      # sub-Director dropped
    assert names[0] == "Warm Exec"                        # engaged outranks overlap
    assert out["contacts"][0]["paths"][0]["kind"] == "engaged"
    assert out["contacts"][1]["paths"][0]["kind"] == "shared_employer"
    assert out["warm_count"] == 2
    assert len(repo.profiles) == 3                        # founders cached after scrape
    assert "founder_profile" in costs and "contact_search" in costs


def test_parse_apollo_builds_stints_from_employment_history():
    item = {"name": "Jane Roe", "title": "VP Revenue Cycle",
            "linkedin": "http://www.linkedin.com/in/jane", "city": "Cincinnati", "state": "Ohio",
            "employment_history": [
                {"org": "TriHealth", "title": "VP", "start": "2017-04-01", "end": None, "current": True},
                {"org": "Olive", "title": "Lead", "start": "2014-01-01", "end": "2017-01-01", "current": False}]}
    contact, exp, edu = profiles.parse_apollo(item)
    assert contact.name == "Jane Roe" and contact.location == "Cincinnati, Ohio"
    assert exp[0].norm == "trihealth" and exp[0].end_year == 9999       # current -> open
    assert exp[1].norm == "olive" and exp[1].start_year == 2014 and exp[1].end_year == 2017
    assert edu == []                                                    # Apollo: no schools
    assert profiles.parse_apollo({}) is None


@pytest.mark.asyncio
async def test_generate_prefers_apollo_and_skips_apify(monkeypatch):
    repo = _FakeDiscoRepo()

    async def fake_founder(url):
        return _founder(exp=[_stint("Olive", 2019, 2022)])

    async def apollo_ok(domain):
        return [{"name": "Olive Alum", "title": "VP Revenue Cycle",
                 "linkedin": "http://www.linkedin.com/in/olive-alum", "city": "Cincinnati", "state": "OH",
                 "employment_history": [{"org": "Olive", "title": "Lead",
                                         "start": "2020-01-01", "end": "2021-01-01", "current": False}]}]

    async def apify_must_not_run(company, limit=None):
        raise AssertionError("Apify must not run when Apollo returns contacts")

    monkeypatch.setattr(service.profiles, "fetch_founder", fake_founder)
    monkeypatch.setattr(service.profiles, "apollo_contacts", apollo_ok)
    monkeypatch.setattr(service.profiles, "search_contacts", apify_must_not_run)

    out = await service.generate({"name": "TriHealth", "domain": "trihealth.com"},
                                 discovery_repo=repo)
    assert out["source"] == "apollo"
    assert out["contacts"][0]["paths"][0]["kind"] == "shared_employer"
    assert "overlapping 2020-2021" in out["contacts"][0]["paths"][0]["evidence"]


# ── persistence ────────────────────────────────────────────────────────


def test_warm_intros_roundtrip_json_repo(tmp_path):
    repo = ScoringJsonRepository(path=str(tmp_path / "s.json"))
    acc = Account(account_id="acc_x", name="X", segment="specialty",
                  framework="specialty", source="csv")
    repo.upsert_account(acc, state="scored")
    repo.set_warm_intros("acc_x", {"state": "ready", "contacts": []})
    assert repo.get("acc_x")["warm_intros"]["state"] == "ready"


def test_founder_profiles_roundtrip_json_repo(tmp_path):
    repo = JsonFileRepository(path=str(tmp_path / "d.json"))
    assert repo.founder_profiles() == []
    n = repo.replace_founder_profiles([{"name": "G", "linkedin_url": "u"}])
    assert n == 1
    assert repo.founder_profiles()[0]["name"] == "G"
