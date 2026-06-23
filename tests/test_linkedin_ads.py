"""LinkedIn TOFU ad-engagement — pure config + the orchestrator (mocked I/O)."""

from __future__ import annotations

import pytest

from auto_search.engagement import linkedin_ads as la
from auto_search.engagement import linkedin_ads_runner as runner
from auto_search.engagement import scoring
from auto_search.engagement.cross import AccountMatch

# ── pure config / mapping ──────────────────────────────────────────────


def test_heat_kind_registered():
    assert scoring.points_for("linkedin_tofu") == 2


def test_category_normalization_and_aliases():
    assert la.normalize_category("Ortho") == "ortho"
    assert la.normalize_category("Behavioral") == "behavioural"      # alias
    assert la.normalize_category("Anesthesiology") == "anesthesia"   # alias
    assert la.normalize_category("Health System") == "health systems"
    assert la.normalize_category(" PAYER ") == "payers"
    assert la.normalize_category("nonsense") is None
    assert la.normalize_category(None) is None


def test_campaign_and_segment_mapping():
    assert la.campaign_for("Ortho") == 1709709
    assert la.campaign_for("Health Systems") == 1709710
    assert la.campaign_for("Payers") == 1709711
    assert la.campaign_for("Behavioural") == 1709712
    assert la.campaign_for("Radiology") == 1709713
    assert la.campaign_for("Anesthesia") == 1709714
    assert la.campaign_for("nope") is None
    assert la.segment_for("Health Systems") == "health_system"
    assert la.segment_for("Payers") == "payer"
    assert la.segment_for("Ortho") == "specialty"


def test_load_share_categories():
    csv_text = ('"share_id","category"\n'
                '"111","Ortho"\n"222","Behavioral"\n"333","Health System"\n'
                '"444","junk-category"\n')   # junk row dropped
    m = la.load_share_categories(csv_text)
    assert m == {"111": "ortho", "222": "behavioural", "333": "health systems"}


def test_load_share_categories_requires_columns():
    with pytest.raises(ValueError):
        la.load_share_categories("foo,bar\n1,2\n")


def test_build_lead_payload_happy_and_required():
    p = la.build_lead_payload(last_name="Abm", first_name="Anna", title="VP RCM",
                              company="ABM Health", phone="+15551112222",
                              email="anna@abmco.com")
    assert p == {"LastName": "Abm", "Company": "ABM Health",
                 "UTM_Campaign__c": "TOFU Engagement Campaign", "FirstName": "Anna",
                 "Title": "VP RCM", "Phone": "+15551112222", "Email": "anna@abmco.com"}
    # optional fields omitted when blank
    p2 = la.build_lead_payload(last_name="X", company="Y")
    assert set(p2) == {"LastName", "Company", "UTM_Campaign__c"}
    for bad in (dict(last_name="", company="Y"), dict(last_name="X", company="  ")):
        with pytest.raises(ValueError):
            la.build_lead_payload(**bad)


def test_post_url():
    assert la.post_url("123") == "https://www.linkedin.com/feed/update/urn:li:share:123"


# ── runner orchestration (mocked Apify / Apollo / cross) ───────────────

_REACTORS = [
    {"name": "Anna Abm", "position": "VP", "linkedin_url": "li/anna", "profile_id": "pa", "reaction_type": "LIKE"},
    {"name": "Mag Staff", "position": "Eng", "linkedin_url": "li/mag", "profile_id": "pb", "reaction_type": "LIKE"},
    {"name": "Carl Cold", "position": "Mgr", "linkedin_url": "li/carl", "profile_id": "pc", "reaction_type": "LIKE"},
    {"name": "Dana Dm", "position": "Dir", "linkedin_url": "li/dana", "profile_id": "pd", "reaction_type": "LIKE"},
    {"name": "Eve Existing", "position": "COO", "linkedin_url": "li/eve", "profile_id": "pe", "reaction_type": "LIKE"},
]
_ENRICH = {
    "li/anna": {"full_name": "Anna Abm", "company": "ABM Health", "company_domain": "abmco.com", "linkedin_url": "li/anna2", "job_title": "VP"},
    "li/mag":  {"full_name": "Mag Staff", "company": "Magical", "company_domain": "getmagical.com", "linkedin_url": "li/mag2"},
    "li/carl": {"full_name": "Carl Cold", "company": "Random Co", "company_domain": "randomco.com", "linkedin_url": "li/carl2"},
    "li/dana": {"full_name": "Dana Dm", "company": "ABM Health", "company_domain": "abmco.com", "linkedin_url": "li/dana2"},
    "li/eve":  {"full_name": "Eve Existing", "company": "ABM Health", "company_domain": "abmco.com", "linkedin_url": "li/eve2"},
}
_EMAILS = {"Abm": "anna@abmco.com", "Existing": "eve@abmco.com"}   # by last name; Dana → none


class _FakeIndex:
    def match(self, *, company=None, domain=None, email=None):
        if domain == "abmco.com":
            return AccountMatch("abm_abmco", company or "ABM Health", "domain", ("abm",))
        return None


class _FakeSFDC:
    def __init__(self, existing=()):
        self.existing = set(existing)
        self.created: list[dict] = []

    def lead_exists(self, email):
        return email in self.existing

    def create_lead(self, fields, *, assignment_rules=True):
        self.created.append(fields)
        return {"id": "00Q_TEST", "success": True}


class _FakeReply:
    def __init__(self):
        self.added: list[dict] = []

    async def add_to_campaign(self, **kw):
        self.added.append(kw)
        return {"ok": True}


@pytest.fixture
def patched(monkeypatch):
    async def fake_fetch(post_url, *, max_items=50, client=None):
        return list(_REACTORS)

    async def fake_enrich(url, *, client=None):
        return _ENRICH.get(url)

    async def fake_match(*, linkedin_url=None, first_name=None, last_name=None,
                         domain=None, reveal_email=True, reveal_phone=False):
        email = _EMAILS.get(last_name)
        return {"email": email, "title": "VP RevCycle", "phone": None} if email else None

    monkeypatch.setattr(runner.social_apify, "fetch_post_reactions", fake_fetch)
    monkeypatch.setattr(runner.social_apify, "enrich", fake_enrich)
    monkeypatch.setattr(runner.apollo, "match_contact", fake_match)
    monkeypatch.setattr(runner, "build_index", lambda s, d: _FakeIndex())


async def test_dry_run_makes_no_writes(patched, monkeypatch):
    crossed = []
    monkeypatch.setattr(runner, "cross_and_persist", lambda **kw: crossed.append(kw) or (0, 0))
    sfdc, reply = _FakeSFDC(existing={"eve@abmco.com"}), _FakeReply()

    out = await runner.run(share_categories={"111": "Ortho"}, engagement_repo=None,
                           scoring_repo=None, discovery_repo=None,
                           sfdc_client=sfdc, replyio_client=reply, dry_run=True)

    assert out["stats"] == {"scanned": 5, "dropped_magical": 1, "not_abm": 1,
                            "no_email": 1, "dupe_skipped": 1, "would_create": 1}
    # the dry-run guarantee: nothing written anywhere
    assert sfdc.created == [] and reply.added == [] and crossed == []
    assert out["results"][0]["email"] == "anna@abmco.com"
    assert out["results"][0]["campaign_id"] == 1709709


async def test_live_run_creates_one_lead_and_records_heat(patched, monkeypatch):
    crossed = []
    monkeypatch.setattr(runner, "cross_and_persist",
                        lambda **kw: crossed.append(kw) or (1, 1))
    sfdc, reply = _FakeSFDC(existing={"eve@abmco.com"}), _FakeReply()

    out = await runner.run(share_categories={"111": "Ortho"}, engagement_repo=object(),
                           scoring_repo=None, discovery_repo=None,
                           sfdc_client=sfdc, replyio_client=reply, dry_run=False)

    # exactly one lead + one reply.io contact (Anna); Eve deduped, others filtered
    assert len(sfdc.created) == 1
    assert sfdc.created[0]["LastName"] == "Abm"
    assert sfdc.created[0]["Company"] == "ABM Health"
    assert sfdc.created[0]["Email"] == "anna@abmco.com"
    assert sfdc.created[0]["UTM_Campaign__c"] == "TOFU Engagement Campaign"
    assert len(reply.added) == 1 and reply.added[0]["campaign_id"] == 1709709
    assert out["results"][0]["sfdc_lead_id"] == "00Q_TEST"
    # heat recorded once, as a linkedin_tofu event worth 2
    assert len(crossed) == 1
    ev = crossed[0]["event_rows"]
    assert len(ev) == 1 and ev[0]["kind"] == "linkedin_tofu" and ev[0]["points"] == 2
    assert ev[0]["raw"]["sfdc_lead_id"] == "00Q_TEST"
    assert ev[0]["raw"]["category"] == "Ortho"


async def test_person_who_likes_two_posts_counts_once(patched, monkeypatch):
    monkeypatch.setattr(runner, "cross_and_persist", lambda **kw: (0, 0))
    out = await runner.run(share_categories={"111": "Ortho", "222": "Payers"},
                           engagement_repo=None, scoring_repo=None, discovery_repo=None,
                           sfdc_client=_FakeSFDC(), replyio_client=_FakeReply(), dry_run=True)
    # same 5 reactors returned for both posts → deduped to 5 scanned, not 10
    assert out["stats"]["scanned"] == 5


# ── regression guards for the deep-QA findings ─────────────────────────


async def test_failed_sfdc_create_records_no_heat(patched, monkeypatch):
    """C1: a Lead create that throws must not push to Reply.io or record heat."""
    crossed = []
    monkeypatch.setattr(runner, "cross_and_persist", lambda **kw: crossed.append(kw) or (1, 1))

    class RaisingSFDC(_FakeSFDC):
        def create_lead(self, fields, *, assignment_rules=True):
            raise RuntimeError("SFDC 400 duplicate rule")

    reply = _FakeReply()
    out = await runner.run(share_categories={"111": "Ortho"}, engagement_repo=object(),
                           scoring_repo=None, discovery_repo=None,
                           sfdc_client=RaisingSFDC(existing={"eve@abmco.com"}),
                           replyio_client=reply, dry_run=False)
    assert out["stats"].get("sfdc_failed") == 1
    assert reply.added == []          # no campaign push when the lead didn't land
    assert crossed == []              # no heat persisted for a lead that failed


async def test_already_processed_profile_skipped_before_spend(patched, monkeypatch):
    """C2: a person already turned into a lead is skipped before the paid enrich."""
    enrich_calls: list[str] = []
    base = runner.social_apify.enrich

    async def spy_enrich(url, *, client=None):
        enrich_calls.append(url)
        return await base(url, client=client)
    monkeypatch.setattr(runner.social_apify, "enrich", spy_enrich)

    class Repo:
        def contacts(self):
            return [{"external_id": "linkedin:pa"}]   # Anna (pid pa) already a lead

    out = await runner.run(share_categories={"111": "Ortho"}, engagement_repo=Repo(),
                           scoring_repo=None, discovery_repo=None,
                           sfdc_client=_FakeSFDC(), replyio_client=_FakeReply(), dry_run=True)
    assert out["stats"].get("already_processed") == 1
    assert "li/anna" not in enrich_calls    # skipped before paying to enrich
    assert all(o["email"] != "anna@abmco.com" for o in out["results"])


async def test_website_url_domain_normalizes_for_abm(patched, monkeypatch):
    """H1: a full-URL / www company_domain must still match an ABM domain."""
    async def fetch_one(post_url, *, max_items=50, client=None):
        return [{"name": "Url Person", "position": "VP", "linkedin_url": "li/url",
                 "profile_id": "pu", "reaction_type": "LIKE"}]

    async def enrich_url(url, *, client=None):
        return {"full_name": "Url Person", "company": "ABM Health",
                "company_domain": "https://www.abmco.com/", "linkedin_url": "li/url2"}

    async def match_url(*, linkedin_url=None, first_name=None, last_name=None,
                        domain=None, reveal_email=True, reveal_phone=False):
        return {"email": "url@abmco.com", "title": "VP", "phone": None}

    monkeypatch.setattr(runner.social_apify, "fetch_post_reactions", fetch_one)
    monkeypatch.setattr(runner.social_apify, "enrich", enrich_url)
    monkeypatch.setattr(runner.apollo, "match_contact", match_url)
    out = await runner.run(share_categories={"111": "Ortho"}, engagement_repo=None,
                           scoring_repo=None, discovery_repo=None,
                           sfdc_client=_FakeSFDC(), replyio_client=_FakeReply(), dry_run=True)
    assert out["stats"].get("would_create") == 1
    assert out["results"][0]["domain"] == "abmco.com"


async def test_enrich_failure_is_isolated(patched, monkeypatch):
    """M4: one profile's enrich error is counted and skipped; the batch completes."""
    async def flaky_enrich(url, *, client=None):
        if url == "li/anna":
            raise RuntimeError("apify 500")
        return _ENRICH.get(url)
    monkeypatch.setattr(runner.social_apify, "enrich", flaky_enrich)
    out = await runner.run(share_categories={"111": "Ortho"}, engagement_repo=None,
                           scoring_repo=None, discovery_repo=None,
                           sfdc_client=_FakeSFDC(existing={"eve@abmco.com"}),
                           replyio_client=_FakeReply(), dry_run=True)
    assert out["stats"].get("enrich_failed") == 1
    assert out["stats"]["scanned"] == 5      # run still completed every candidate


async def test_replyio_409_is_not_a_failure(patched, monkeypatch):
    """A Reply.io 409 (contact already in another sequence) is expected, not a failure;
    the SFDC Lead + heat still land."""
    monkeypatch.setattr(runner, "cross_and_persist", lambda **kw: (1, 1))

    class Reply409:
        def __init__(self):
            self.calls = 0

        async def add_to_campaign(self, **kw):
            self.calls += 1
            return {"status": 409, "detail": "already in another sequence"}

    reply = Reply409()
    out = await runner.run(share_categories={"111": "Ortho"}, engagement_repo=object(),
                           scoring_repo=None, discovery_repo=None,
                           sfdc_client=_FakeSFDC(existing={"eve@abmco.com"}),
                           replyio_client=reply, dry_run=False)
    assert out["stats"].get("sfdc_created") == 1
    assert out["stats"].get("replyio_already_sequenced") == 1
    assert out["stats"].get("replyio_failed") is None     # 409 is not counted as a failure
    assert reply.calls == 1


async def test_max_leads_caps_output(patched, monkeypatch):
    """max_leads stops after that many leads — the safety cap for the live spot-check."""
    async def fetch_two(post_url, *, max_items=50, client=None):
        return [{"name": "One A", "linkedin_url": "li/1", "profile_id": "p1", "position": "VP", "reaction_type": "LIKE"},
                {"name": "Two B", "linkedin_url": "li/2", "profile_id": "p2", "position": "VP", "reaction_type": "LIKE"}]

    async def enrich_two(url, *, client=None):
        return {"full_name": "X Y", "company": "ABM Health", "company_domain": "abmco.com", "linkedin_url": url}

    async def match_two(*, linkedin_url=None, first_name=None, last_name=None,
                        domain=None, reveal_email=True, reveal_phone=False):
        return {"email": f"x{linkedin_url}@abmco.com", "title": "VP", "phone": None}

    monkeypatch.setattr(runner.social_apify, "fetch_post_reactions", fetch_two)
    monkeypatch.setattr(runner.social_apify, "enrich", enrich_two)
    monkeypatch.setattr(runner.apollo, "match_contact", match_two)
    out = await runner.run(share_categories={"111": "Ortho"}, engagement_repo=None,
                           scoring_repo=None, discovery_repo=None, sfdc_client=_FakeSFDC(),
                           replyio_client=_FakeReply(), dry_run=True, max_leads=1)
    assert out["stats"]["would_create"] == 1   # capped at 1, not 2
