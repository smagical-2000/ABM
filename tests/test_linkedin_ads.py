"""LinkedIn TOFU ad-engagement — pure config + the orchestrator (mocked I/O).

The flow pushes to Airtable (the "LinkedIn <> Airtable" table) + Reply.io and records
`linkedin_tofu` heat. It does NOT touch Salesforce (downstream Airtable automation
handles that). Dedup is the profile-id gate + Airtable's upsert-on-Email.
"""

from __future__ import annotations

import pytest

from auto_search.engagement import linkedin_ads as la
from auto_search.engagement import linkedin_ads_runner as runner
from auto_search.engagement import scoring
from auto_search.engagement.cross import AccountMatch

# ── pure config / mapping ──────────────────────────────────────────────


def test_heat_kind_registered():
    assert scoring.points_for("linkedin_tofu") == 6


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


def test_build_airtable_fields_happy_and_required():
    f = la.build_airtable_fields(email="anna@abmco.com", company="ABM Health",
                                 first_name="Anna", last_name="Abm", title="VP RCM",
                                 phone="+15551112222", linkedin_url="li/anna2")
    assert f == {"Email": "anna@abmco.com", "Company Name": "ABM Health",
                 "UTM Source": "linkedin", "UTM Medium": "paid-social",
                 "UTM Campaign": "TOFU Engagement Campaign", "ABM Match": "Yes",
                 "First Name": "Anna", "Last Name": "Abm", "Title": "VP RCM",
                 "Phone": "+15551112222", "LinkedIn URL": "li/anna2"}
    # optional fields omitted when blank
    f2 = la.build_airtable_fields(email="x@y.com", company="Y")
    assert set(f2) == {"Email", "Company Name", "UTM Source", "UTM Medium",
                       "UTM Campaign", "ABM Match"}
    # a key (Email OR LinkedIn URL) + Company Name are required
    for bad in (dict(email="", company="Y"), dict(email="x@y.com", company="  ")):
        with pytest.raises(ValueError):
            la.build_airtable_fields(**bad)


def test_build_airtable_fields_phone_only_and_non_abm():
    """2026-07-08 rules: a phone-only lead keys on LinkedIn URL (no Email cell
    written), and non-ABM captures are tagged ABM Match: No."""
    f = la.build_airtable_fields(company="Random Co", phone="+15550001111",
                                 linkedin_url="li/carl2", abm_match=False)
    assert "Email" not in f
    assert f["ABM Match"] == "No" and f["LinkedIn URL"] == "li/carl2"
    with pytest.raises(ValueError):     # no email AND no linkedin url = no key
        la.build_airtable_fields(company="Random Co", phone="+15550001111")


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
# by last name; Dana → none (ABM, phone-only path), Carl → non-ABM WITH email
_EMAILS = {"Abm": "anna@abmco.com", "Existing": "eve@abmco.com", "Cold": "carl@randomco.com"}


class _FakeIndex:
    def match(self, *, company=None, domain=None, email=None,
              trust_declared=False):
        if domain == "abmco.com":
            return AccountMatch("abm_abmco", company or "ABM Health", "domain", ("abm",))
        return None


class _FakeAirtable:
    def __init__(self, fail=False):
        self.upserts: list[dict] = []
        self._fail = fail

    async def upsert(self, fields, *, merge_on):
        if self._fail:
            raise RuntimeError("airtable 422 unprocessable")
        self.upserts.append({"fields": fields, "merge_on": merge_on})
        return {"records": [{"id": "recTEST", "fields": fields}]}

    @staticmethod
    def record_id(resp):
        recs = (resp or {}).get("records") or []
        return recs[0].get("id") if recs else None


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

    async def fake_fe_none(**kw):
        return {}   # no phone found; tests override when they want one

    monkeypatch.setattr(runner.social_apify, "fetch_post_reactions", fake_fetch)
    monkeypatch.setattr(runner.social_apify, "enrich", fake_enrich)
    monkeypatch.setattr(runner.apollo, "match_contact", fake_match)
    # Always patched: the email-or-phone rule reaches FullEnrich for no-email
    # leads, and a test must NEVER hit the real (billable) API.
    monkeypatch.setattr(runner.phone_waterfall.enrichment, "enrich_contact", fake_fe_none)
    monkeypatch.setattr(runner, "build_index", lambda s, d, e=None: _FakeIndex())


async def test_dry_run_makes_no_writes(patched, monkeypatch):
    crossed = []
    monkeypatch.setattr(runner, "cross_and_persist", lambda **kw: crossed.append(kw) or (0, 0))
    air, reply = _FakeAirtable(), _FakeReply()

    out = await runner.run(share_categories={"111": "Ortho"}, engagement_repo=None,
                           scoring_repo=None, discovery_repo=None,
                           airtable_client=air, replyio_client=reply, dry_run=True)

    # Anna + Eve are ABM with email; Carl is non-ABM with email (captured since
    # 2026-07-08); Mag dropped; Dana has no email and dry runs never spend
    # FullEnrich, so she has no phone either → not a lead.
    assert out["stats"] == {"scanned": 5, "dropped_magical": 1, "non_abm_captured": 1,
                            "no_email_or_phone": 1, "would_create": 3}
    # the dry-run guarantee: nothing written anywhere
    assert air.upserts == [] and reply.added == [] and crossed == []
    assert out["results"][0]["email"] == "anna@abmco.com"
    assert out["results"][0]["campaign_id"] == 1709709


async def test_live_run_upserts_and_records_heat(patched, monkeypatch):
    crossed = []
    monkeypatch.setattr(runner, "cross_and_persist",
                        lambda **kw: crossed.append(kw) or (2, 2))
    air, reply = _FakeAirtable(), _FakeReply()

    out = await runner.run(share_categories={"111": "Ortho"}, engagement_repo=object(),
                           scoring_repo=None, discovery_repo=None,
                           airtable_client=air, replyio_client=reply, dry_run=False, allow_empty_store=True)

    # three leads with email hit Airtable (Anna + Eve ABM, Carl non-ABM) — but
    # ONLY the two ABM matches enter Reply.io and earn heat events
    assert len(air.upserts) == 3
    anna = air.upserts[0]["fields"]
    assert anna["First Name"] == "Anna" and anna["Last Name"] == "Abm"
    assert anna["Email"] == "anna@abmco.com" and anna["Company Name"] == "ABM Health"
    assert anna["UTM Campaign"] == "TOFU Engagement Campaign"
    assert anna["LinkedIn URL"] == "li/anna2"          # enriched URL
    assert anna["ABM Match"] == "Yes"
    assert air.upserts[0]["merge_on"] == ["Email"]     # idempotent on email
    carl = air.upserts[1]["fields"]
    assert carl["ABM Match"] == "No" and carl["Company Name"] == "Random Co"
    assert len(reply.added) == 2 and reply.added[0]["campaign_id"] == 1709709
    assert {a["email"] for a in reply.added} == {"anna@abmco.com", "eve@abmco.com"}
    assert out["results"][0]["airtable_id"] == "recTEST"
    # heat recorded once for the batch, as linkedin_tofu events worth 6 (TOFU
    # lead) — ABM matches only; Carl produces a contact row but NO event
    assert len(crossed) == 1
    ev = crossed[0]["event_rows"]
    assert len(ev) == 2 and ev[0]["kind"] == "linkedin_tofu" and ev[0]["points"] == 6
    assert ev[0]["raw"]["airtable_id"] == "recTEST"
    assert ev[0]["raw"]["category"] == "Ortho"
    # every capture (incl. Carl + dead-end Dana) is a contact row = durable dedup
    assert len(crossed[0]["contact_rows"]) == 4


async def test_fullenrich_fills_phone_to_airtable_on_live(patched, monkeypatch):
    """Apollo returns no phone → FullEnrich resolves the mobile, and it lands on the
    Airtable upsert (which syncs to Salesforce) and Reply.io. Flow unchanged; phone added."""
    calls = []

    async def fake_fe(*, first_name=None, last_name=None, domain=None,
                      company=None, linkedin=None, http=None):
        calls.append(last_name)
        return {"email": None, "phone": "+15557654321"}

    monkeypatch.setattr(runner.phone_waterfall.enrichment, "enrich_contact", fake_fe)
    monkeypatch.setattr(runner, "cross_and_persist", lambda **kw: (2, 2))
    air, reply = _FakeAirtable(), _FakeReply()
    out = await runner.run(share_categories={"111": "Ortho"}, engagement_repo=object(),
                           scoring_repo=None, discovery_repo=None,
                           airtable_client=air, replyio_client=reply, dry_run=False, allow_empty_store=True)
    assert calls                                                  # FullEnrich ran for no-phone leads
    assert air.upserts[0]["fields"]["Phone"] == "+15557654321"    # → Airtable → Salesforce
    assert reply.added[0]["phone"] == "+15557654321"             # → Reply.io
    # THE FIX (2026-07-09): Carl (non-ABM, has email) now DOES get a phone
    # lookup — ABM status no longer gates the paid tier.
    assert "Cold" in calls
    # Dana (ABM, no email) is SAVED by the phone: phone-only row keyed on
    # LinkedIn URL, no Email cell, and no Reply.io enrollment (email tool)
    dana = next(u for u in air.upserts if u["fields"].get("First Name") == "Dana")
    assert "Email" not in dana["fields"] and dana["merge_on"] == ["LinkedIn URL"]
    assert dana["fields"]["ABM Match"] == "Yes"
    assert out["stats"].get("replyio_skipped_no_email") == 1
    assert all(a.get("email") for a in reply.added)               # email leads only


async def test_fullenrich_not_called_on_dry_run(patched, monkeypatch):
    """Dry runs never spend FullEnrich credits."""
    calls = []

    async def fake_fe(**kw):
        calls.append(1)
        return {"email": None, "phone": "x"}

    monkeypatch.setattr(runner.phone_waterfall.enrichment, "enrich_contact", fake_fe)
    air, reply = _FakeAirtable(), _FakeReply()
    await runner.run(share_categories={"111": "Ortho"}, engagement_repo=None,
                     scoring_repo=None, discovery_repo=None,
                     airtable_client=air, replyio_client=reply, dry_run=True)
    assert calls == []


async def test_person_who_likes_two_posts_counts_once(patched, monkeypatch):
    monkeypatch.setattr(runner, "cross_and_persist", lambda **kw: (0, 0))
    out = await runner.run(share_categories={"111": "Ortho", "222": "Payers"},
                           engagement_repo=None, scoring_repo=None, discovery_repo=None,
                           airtable_client=_FakeAirtable(), replyio_client=_FakeReply(),
                           dry_run=True)
    # same 5 reactors returned for both posts → deduped to 5 scanned, not 10
    assert out["stats"]["scanned"] == 5


# ── regression guards for the deep-QA findings ─────────────────────────


async def test_failed_airtable_upsert_records_no_heat(patched, monkeypatch):
    """C1: an Airtable push that throws must not push to Reply.io or record heat."""
    crossed = []
    monkeypatch.setattr(runner, "cross_and_persist", lambda **kw: crossed.append(kw) or (1, 1))
    reply = _FakeReply()

    out = await runner.run(share_categories={"111": "Ortho"}, engagement_repo=object(),
                           scoring_repo=None, discovery_repo=None,
                           airtable_client=_FakeAirtable(fail=True),
                           replyio_client=reply, dry_run=False, allow_empty_store=True)
    assert out["stats"].get("airtable_failed") == 3   # all email leads (Anna, Carl, Eve) fail
    assert reply.added == []          # no campaign push when the row didn't land
    # Dana's dead-end contact row (dedup) is all that persists — no events, so
    # no heat could be recorded for pushes that failed
    assert all(c["event_rows"] == [] for c in crossed)


async def test_no_slack_card_when_the_durable_write_fails(patched, monkeypatch):
    """The card used to post BEFORE the Airtable upsert. On an Airtable failure
    the loop continues without persisting the contact row, so the person is
    absent from the dedup set next run — and the identical lead card was
    re-posted to the leads-ads channel every 15 minutes until Airtable
    recovered. The durable write goes first now."""
    monkeypatch.setattr(runner, "cross_and_persist", lambda **kw: (1, 1))
    cards: list[dict] = []
    monkeypatch.setattr(runner.notify, "notify_lead",
                        lambda lead, **kw: cards.append(lead) or True)

    out = await runner.run(share_categories={"111": "Ortho"}, engagement_repo=object(),
                           scoring_repo=None, discovery_repo=None,
                           airtable_client=_FakeAirtable(fail=True),
                           replyio_client=_FakeReply(), dry_run=False,
                           allow_empty_store=True)
    assert out["stats"].get("airtable_failed") == 3
    assert cards == []                       # nothing announced that didn't land
    assert not out["stats"].get("slack_notified")


async def test_slack_card_follows_the_airtable_upsert(patched, monkeypatch):
    """Ordering, not just counts: every card is preceded by its own row landing
    in Airtable (the Airtable automation is what creates the Salesforce lead,
    so the card is still the heads-up 'before Salesforce')."""
    monkeypatch.setattr(runner, "cross_and_persist", lambda **kw: (3, 3))
    order: list[str] = []

    class _OrderedAirtable(_FakeAirtable):
        async def upsert(self, fields, *, merge_on):
            order.append("airtable")
            return await super().upsert(fields, merge_on=merge_on)

    monkeypatch.setattr(runner.notify, "notify_lead",
                        lambda lead, **kw: order.append("slack") or True)

    out = await runner.run(share_categories={"111": "Ortho"}, engagement_repo=object(),
                           scoring_repo=None, discovery_repo=None,
                           airtable_client=_OrderedAirtable(),
                           replyio_client=_FakeReply(), dry_run=False,
                           allow_empty_store=True)
    assert out["stats"]["slack_notified"] == 3
    assert order == ["airtable", "slack"] * 3


async def test_already_processed_profile_skipped_before_spend(patched, monkeypatch):
    """C2: a person already pushed is skipped before the paid enrich."""
    enrich_calls: list[str] = []
    base = runner.social_apify.enrich

    async def spy_enrich(url, *, client=None):
        enrich_calls.append(url)
        return await base(url, client=client)
    monkeypatch.setattr(runner.social_apify, "enrich", spy_enrich)

    class Repo:
        def contacts(self):
            return [{"external_id": "linkedin:pa"}]   # Anna (pid pa) already pushed

    out = await runner.run(share_categories={"111": "Ortho"}, engagement_repo=Repo(),
                           scoring_repo=None, discovery_repo=None,
                           airtable_client=_FakeAirtable(), replyio_client=_FakeReply(),
                           dry_run=True)
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
                           airtable_client=_FakeAirtable(), replyio_client=_FakeReply(),
                           dry_run=True)
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
                           airtable_client=_FakeAirtable(), replyio_client=_FakeReply(),
                           dry_run=True)
    assert out["stats"].get("enrich_failed") == 1
    assert out["stats"]["scanned"] == 5      # run still completed every candidate


async def test_replyio_409_is_not_a_failure(patched, monkeypatch):
    """A Reply.io 409 (contact already in another sequence) is expected, not a failure;
    the Airtable upsert + heat still land."""
    monkeypatch.setattr(runner, "cross_and_persist", lambda **kw: (2, 2))

    class Reply409:
        def __init__(self):
            self.calls = 0

        async def add_to_campaign(self, **kw):
            self.calls += 1
            return {"status": 409, "detail": "already in another sequence"}

    reply = Reply409()
    out = await runner.run(share_categories={"111": "Ortho"}, engagement_repo=object(),
                           scoring_repo=None, discovery_repo=None,
                           airtable_client=_FakeAirtable(), replyio_client=reply,
                           dry_run=False, allow_empty_store=True)
    assert out["stats"].get("airtable_upserted") == 3     # Anna, Carl, Eve rows land
    assert out["stats"].get("replyio_already_sequenced") == 2   # ABM leads only
    assert out["stats"].get("replyio_failed") is None     # 409 is not counted as a failure
    assert reply.calls == 2


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
                           scoring_repo=None, discovery_repo=None,
                           airtable_client=_FakeAirtable(), replyio_client=_FakeReply(),
                           dry_run=True, max_leads=1)
    assert out["stats"]["would_create"] == 1   # capped at 1, not 2


# ── the tracking mirror (Galyna's "TOFU Leads by ABM" base, 2026-07-08) ─


async def test_mirror_receives_copy_with_synced_at(patched, monkeypatch):
    """Every lead that lands in the primary table is also written to the
    mirror, stamped Synced At; the mirror never gets rows the primary lacks."""
    monkeypatch.setattr(runner, "cross_and_persist", lambda **kw: (2, 2))
    air, mir = _FakeAirtable(), _FakeAirtable()
    out = await runner.run(share_categories={"111": "Ortho"}, engagement_repo=object(),
                           scoring_repo=None, discovery_repo=None,
                           airtable_client=air, replyio_client=_FakeReply(),
                           mirror_client=mir, dry_run=False, allow_empty_store=True)
    # 3 = Anna + Eve (ABM) + Carl (non-ABM, captured since 2026-07-08) — the
    # mirror gets EVERY captured lead, ABM or not, same as the primary.
    assert len(mir.upserts) == len(air.upserts) == 3
    assert out["stats"]["mirror_upserted"] == 3
    m = mir.upserts[0]["fields"]
    assert m["Email"] == "anna@abmco.com" and m["Synced At"]
    assert mir.upserts[0]["merge_on"] == ["Email"]
    carl = mir.upserts[1]["fields"]
    assert carl["ABM Match"] == "No" and carl["Synced At"]


async def test_mirror_failure_never_blocks_the_lead(patched, monkeypatch):
    monkeypatch.setattr(runner, "cross_and_persist", lambda **kw: (2, 2))
    air, mir = _FakeAirtable(), _FakeAirtable(fail=True)
    out = await runner.run(share_categories={"111": "Ortho"}, engagement_repo=object(),
                           scoring_repo=None, discovery_repo=None,
                           airtable_client=air, replyio_client=_FakeReply(),
                           mirror_client=mir, dry_run=False, allow_empty_store=True)
    assert len(air.upserts) == 3                       # primary rows all landed
    assert out["stats"]["mirror_failed"] == 3          # counted for the ops alert
    assert out["stats"]["airtable_upserted"] == 3


async def test_no_mirror_client_means_no_mirror_stats(patched, monkeypatch):
    monkeypatch.setattr(runner, "cross_and_persist", lambda **kw: (2, 2))
    out = await runner.run(share_categories={"111": "Ortho"}, engagement_repo=object(),
                           scoring_repo=None, discovery_repo=None,
                           airtable_client=_FakeAirtable(), replyio_client=_FakeReply(),
                           dry_run=False, allow_empty_store=True)
    assert "mirror_upserted" not in out["stats"] and "mirror_failed" not in out["stats"]


class _FakeEngRepo:
    """Records what actually persists — used with the REAL cross_and_persist."""

    def __init__(self):
        self.contact_rows: list[dict] = []
        self.event_rows: list[dict] = []

    def contacts(self):
        return list(self.contact_rows)

    def upsert_contact(self, c):
        self.contact_rows.append(c)

    def add_event(self, e):
        self.event_rows.append(e)
        return True


async def test_capture_all_persists_non_abm_contacts_for_dedup(patched, monkeypatch):
    """Regression (QA, 2026-07-08): every other runner test monkeypatches
    cross_and_persist and asserts on its INPUT, which twice let the real
    function silently drop non-ABM contacts (persist_unmatched defaults to
    False). This test runs the REAL cross_and_persist against a recording
    repo: Carl (non-ABM) must land as a contact row — it is the durable dedup
    key; without it every scan re-bills Apollo/FullEnrich for him — while
    heat events stay ABM-only."""
    from auto_search.engagement import sync as sync_mod
    monkeypatch.setattr(sync_mod, "build_index", lambda s, d, e=None: _FakeIndex())
    repo = _FakeEngRepo()
    out = await runner.run(share_categories={"111": "Ortho"}, engagement_repo=repo,
                           scoring_repo=None, discovery_repo=None,
                           airtable_client=_FakeAirtable(), replyio_client=_FakeReply(),
                           dry_run=False, allow_empty_store=True)
    exts = {c["external_id"] for c in repo.contact_rows}
    # Anna + Eve (ABM leads), Carl (non-ABM lead), Dana (dead-end, no contact info)
    assert exts == {"linkedin:pa", "linkedin:pe", "linkedin:pc", "linkedin:pd"}
    carl = next(c for c in repo.contact_rows if c["external_id"] == "linkedin:pc")
    assert carl.get("account_id") is None            # captured, but no account cross
    assert len(repo.event_rows) == 2                 # heat is ABM-only (Anna + Eve)
    assert all(e["account_id"] == "abm_abmco" for e in repo.event_rows)
    assert out["stats"]["heat_events"] == 2


async def test_live_run_refuses_empty_dedup_store(patched):
    """Guard (2026-07-08 duplicate-cards incident): a LIVE run that sees ZERO
    known contacts is almost certainly talking to the wrong/unreachable store —
    an empty dedup list would re-post every Slack card and re-bill every
    Apollo/FullEnrich lookup. Abort BEFORE any scrape/spend; a genuinely fresh
    store must opt in with allow_empty_store=True. Dry runs are unaffected."""
    with pytest.raises(RuntimeError, match="ZERO known contacts"):
        await runner.run(share_categories={"111": "Ortho"}, engagement_repo=object(),
                         scoring_repo=None, discovery_repo=None,
                         airtable_client=_FakeAirtable(), replyio_client=_FakeReply(),
                         dry_run=False)


def test_general_category_recognized_but_unmapped():
    """'general' (a cross-segment post) is a valid category but has no single
    Reply.io campaign or segment (Sunny, 2026-07-09)."""
    assert la.normalize_category("General") == "general"
    assert la.normalize_category("all categories") == "general"
    assert la.normalize_category("Mixed") == "general"
    assert la.campaign_for("general") is None
    assert la.segment_for("general") is None


async def test_general_post_captures_and_heats_but_no_replyio(patched, monkeypatch):
    """A 'general' post: ABM reactors are still captured to Airtable and heat-
    scored, but NOBODY is auto-enrolled in Reply.io (no one campaign fits a
    cross-segment post) — enrollment is left to SDR/Clay routing."""
    monkeypatch.setattr(runner, "cross_and_persist", lambda **kw: (2, 2))
    air, reply = _FakeAirtable(), _FakeReply()
    out = await runner.run(share_categories={"111": "General"}, engagement_repo=object(),
                           scoring_repo=None, discovery_repo=None,
                           airtable_client=air, replyio_client=reply,
                           dry_run=False, allow_empty_store=True)
    assert len(air.upserts) == 3          # Anna + Carl + Eve captured as usual
    assert reply.added == []              # no campaign for a general post → no enrollment
    assert out["stats"].get("heat_events") == 2   # ABM heat still recorded


async def test_fullenrich_cap_counts_attempts_not_hits(patched, monkeypatch):
    """QA 2026-07-09: a FullEnrich MISS is still a billed lookup. With the cap at
    1 and a provider that never finds phones, exactly ONE paid call happens for
    the whole run — the rest are capped, not silently retried."""
    calls = []

    async def fe_always_miss(**kw):
        calls.append(kw.get("last_name"))
        return {"email": None, "phone": None}

    monkeypatch.setattr(runner.phone_waterfall.enrichment, "enrich_contact", fe_always_miss)
    monkeypatch.setattr(runner, "cross_and_persist", lambda **kw: (0, 0))
    monkeypatch.setenv("LINKEDIN_TOFU_FULLENRICH_MAX", "1")
    out = await runner.run(share_categories={"111": "Ortho"}, engagement_repo=object(),
                           scoring_repo=None, discovery_repo=None,
                           airtable_client=_FakeAirtable(), replyio_client=_FakeReply(),
                           dry_run=False, allow_empty_store=True)
    assert len(calls) == 1                                    # one billed attempt, then capped
    assert out["stats"]["fullenrich_lookups"] == 1
    assert out["stats"]["fullenrich_capped"] >= 1             # later leads were held back
