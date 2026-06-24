"""Engagement sync orchestrator (Milestone F) — pull -> ingest -> cross -> store.

Uses fakes for the Reply.io client + the scored/ABM repos, so it exercises the
whole pipeline with no network and a real JSON engagement store.
"""

import pytest

from auto_search.db.engagement_repository import EngagementJsonRepository
from auto_search.engagement import sync as sync_mod


class _FakeClient:
    def __init__(self, contacts, activity):
        self._contacts, self._activity = contacts, activity

    async def iter_contacts(self, *, top=1000):
        for c in self._contacts:
            yield c

    async def iter_email_activity(self, *, date_from, date_to, top=200):
        for a in self._activity:
            yield a


class _FakeScoring:
    def list_accounts(self):
        return [{"account_id": "acc_christus", "name": "CHRISTUS Health",
                 "domain": "christushealth.org"}]


class _FakeDiscovery:
    def abm_targets(self):
        return [{"name": "Newport Healthcare", "keys": ["newporthealthcare"],
                 "domain": "newporthealthcare.com"}]


@pytest.mark.asyncio
async def test_run_sync_pulls_normalizes_crosses_stores(tmp_path):
    repo = EngagementJsonRepository(path=str(tmp_path / "eng.json"))
    contacts = [{"id": 1, "email": "g@christushealth.org", "domain": "christushealth.org",
                 "company": "CHRISTUS Health", "meetingStatus": "meetingBooked"}]
    activity = [
        {"contactId": 1, "company": "CHRISTUS Health", "email": "g@christushealth.org",
         "sequenceName": "Q2", "deliveryDate": "2026-06-05T00:00:00Z",
         "isDelivered": True, "isClicked": True, "isReplied": True},
        {"contactId": 2, "company": "Newport Healthcare", "email": "x@newporthealthcare.com",
         "deliveryDate": "2026-06-06T00:00:00Z", "isDelivered": True, "isReplied": True},
        {"contactId": 3, "company": "Random Co", "email": "y@randomco.com",
         "deliveryDate": "2026-06-06T00:00:00Z", "isDelivered": True, "isClicked": True},
    ]
    stats = await sync_mod.run_sync(
        engagement_repo=repo, scoring_repo=_FakeScoring(), discovery_repo=_FakeDiscovery(),
        client=_FakeClient(contacts, activity), max_contacts=None,
        now="2026-06-14T00:00:00Z")

    assert stats["activity_rows"] == 3
    assert stats["matched_contacts"] == 2 and stats["unresolved_contacts"] == 1

    accts = {a["account_id"]: a for a in repo.engaged_accounts()}
    assert accts["acc_christus"]["score"] == 17        # click 1 + reply 6 + meeting 10 (scored)
    assert accts["abm_newporthealthcare"]["score"] == 6  # reply 6 (ABM-only)
    assert not any(a.startswith("Random") for a in accts)  # unmatched -> not engaged

    assert repo.get_sync_state()["status"] == "success"


@pytest.mark.asyncio
async def test_run_sync_pulls_from_since_floor(tmp_path):
    repo = EngagementJsonRepository(path=str(tmp_path / "e2.json"))
    calls = []

    class _Client:
        async def iter_contacts(self, *, top=1000):
            return
            yield  # pragma: no cover (max_contacts=0 skips the roster)

        async def iter_email_activity(self, *, date_from, date_to, top=200):
            calls.append(date_from)
            yield {"contactId": 1, "company": "CHRISTUS Health", "email": "g@christushealth.org",
                   "isDelivered": True, "isReplied": True, "deliveryDate": "2026-03-10T00:00:00Z"}

    stats = await sync_mod.run_sync(
        engagement_repo=repo, scoring_repo=_FakeScoring(), discovery_repo=_FakeDiscovery(),
        client=_Client(), since="2026-01-01", max_contacts=0, now="2026-06-14T00:00:00Z")
    # one pull from the Jan-2026 floor (no rolling-window widening)
    assert len(calls) == 1 and calls[0].date().isoformat() == "2026-01-01"
    assert stats["window_from"] == "2026-01-01"


@pytest.mark.asyncio
async def test_run_sync_records_failure(tmp_path):
    repo = EngagementJsonRepository(path=str(tmp_path / "e3.json"))

    class _BoomClient:
        async def iter_email_activity(self, *, date_from, date_to, top=200):
            raise RuntimeError("boom")
            yield  # pragma: no cover

        async def iter_contacts(self, *, top=1000):
            return
            yield  # pragma: no cover

    with pytest.raises(RuntimeError):
        await sync_mod.run_sync(engagement_repo=repo, scoring_repo=_FakeScoring(),
                                discovery_repo=_FakeDiscovery(), client=_BoomClient())
    assert repo.get_sync_state()["status"] == "failed"


# ── podcast sync (same source-agnostic cross + store path) ──────────────


def _podcast_rows():
    return [
        {"Submit Date": "2026-02-01 00:00:00", "Work Email": "a@christushealth.org",
         "ICP": "Yes", "Account Name": "CHRISTUS Health", "Lead Form": "Podcast Vanessa"},
        {"Submit Date": "2026-02-02 00:00:00", "Work Email": "d@christushealth.org",
         "ICP": "Yes", "Account Name": ""},
        {"Submit Date": "2026-02-03 00:00:00", "Work Email": "b@newporthealthcare.com",
         "ICP": "Maybe", "Account Name": ""},
        {"Submit Date": "2026-02-04 00:00:00", "Work Email": "c@randomco.com",
         "ICP": "Yes", "Account Name": ""},
        {"Submit Date": "2026-02-05 00:00:00", "Work Email": "skip@x.com", "ICP": "No"},
    ]


def test_run_podcast_sync_crosses_and_scores(tmp_path):
    repo = EngagementJsonRepository(path=str(tmp_path / "pod.json"))
    stats = sync_mod.run_podcast_sync(
        engagement_repo=repo, scoring_repo=_FakeScoring(), discovery_repo=_FakeDiscovery(),
        rows=_podcast_rows(), now="2026-06-14T00:00:00Z")

    assert stats["contacts"] == 4                      # Yes/Maybe with email (No dropped)
    assert stats["matched_contacts"] == 3 and stats["unresolved_contacts"] == 1

    accts = {a["account_id"]: a for a in repo.engaged_accounts()}
    assert accts["acc_christus"]["score"] == 8         # two podcast leads x 4 (scored)
    assert accts["abm_newporthealthcare"]["score"] == 4  # one lead (ABM-only)
    assert not any(a.startswith("Random") for a in accts)  # unmatched -> Resolve, not engaged
    assert repo.get_sync_state("podcast")["status"] == "success"


def test_run_podcast_sync_is_idempotent(tmp_path):
    repo = EngagementJsonRepository(path=str(tmp_path / "pod2.json"))
    first = sync_mod.run_podcast_sync(
        engagement_repo=repo, scoring_repo=_FakeScoring(), discovery_repo=_FakeDiscovery(),
        rows=_podcast_rows(), now="2026-06-14T00:00:00Z")
    second = sync_mod.run_podcast_sync(
        engagement_repo=repo, scoring_repo=_FakeScoring(), discovery_repo=_FakeDiscovery(),
        rows=_podcast_rows(), now="2026-06-14T00:00:00Z")
    # only the 3 matched leads persist (unmatched dropped); re-sync adds nothing
    assert first["new_events"] == 3 and second["new_events"] == 0
    # podcast + replyio keep independent sync cursors
    assert repo.get_sync_state("podcast")["status"] == "success"


@pytest.mark.asyncio
async def test_podcast_and_replyio_coexist_on_same_account(tmp_path):
    repo = EngagementJsonRepository(path=str(tmp_path / "pod3.json"))
    # email reply (6) on CHRISTUS via Reply.io...
    activity = [{"contactId": 1, "company": "CHRISTUS Health", "email": "g@christushealth.org",
                 "deliveryDate": "2026-06-05T00:00:00Z", "isDelivered": True, "isReplied": True}]
    await sync_mod.run_sync(
        engagement_repo=repo, scoring_repo=_FakeScoring(), discovery_repo=_FakeDiscovery(),
        client=_FakeClient([], activity), max_contacts=0, now="2026-06-14T00:00:00Z")
    # ...plus a podcast lead (4) on the same account
    sync_mod.run_podcast_sync(
        engagement_repo=repo, scoring_repo=_FakeScoring(), discovery_repo=_FakeDiscovery(),
        rows=[{"Submit Date": "2026-02-01 00:00:00", "Work Email": "p@christushealth.org",
               "ICP": "Yes"}], now="2026-06-14T00:00:00Z")
    accts = {a["account_id"]: a for a in repo.engaged_accounts()}
    assert accts["acc_christus"]["score"] == 10        # reply 6 + podcast 4, combined


# ── podcast via published CSV URL (read-only GET → same sync path) ──────


class _FakeResp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class _FakeHttp:
    def __init__(self, text):
        self._text = text
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        return _FakeResp(self._text)


def test_run_podcast_url_sync_fetches_parses_and_crosses(tmp_path):
    repo = EngagementJsonRepository(path=str(tmp_path / "podurl.json"))
    csv_text = (
        "ICP,Work Email,Account Name,Submit Date\n"
        "Yes,a@christushealth.org,CHRISTUS Health,2026-05-01\n"
        "No,b@randomco.com,Random Co,2026-05-02\n")          # ICP No → skipped
    http = _FakeHttp(csv_text)
    stats = sync_mod.run_podcast_url_sync(
        engagement_repo=repo, scoring_repo=_FakeScoring(), discovery_repo=_FakeDiscovery(),
        url="https://docs.google.com/x/pub?output=csv", http=http, now="2026-06-14T00:00:00Z")
    assert http.calls == ["https://docs.google.com/x/pub?output=csv"]   # GET the published URL
    assert stats["new_events"] == 1 and stats["matched_contacts"] == 1   # only the ICP-Yes lead
    accts = {a["account_id"]: a for a in repo.engaged_accounts()}
    assert accts["acc_christus"]["score"] == 4               # one ICP-Yes podcast lead x 4
    assert "randomco" not in " ".join(accts)                 # ICP No never persisted
    e = repo.events_for_account("acc_christus")
    assert any(ev["kind"] == "podcast_lead" and ev["points"] == 4 for ev in e)


# ── sfdc sync (same source-agnostic cross + store path) ─────────────────


class _FakeSfdcClient:
    def __init__(self, leads, tradeshow=None, low_intent=None, meetings=None):
        self._leads = leads
        self._ts = tradeshow or []
        self._lo = low_intent or []
        self._meetings = meetings or []

    def iter_high_intent_leads(self, *, since="2026-01-01"):
        yield from self._leads

    def iter_tradeshow_leads(self, *, since="2026-01-01"):
        yield from self._ts

    def iter_low_intent_leads(self, *, since="2026-01-01"):
        yield from self._lo

    def iter_meetings(self, *, days=180):
        yield from self._meetings


def _sfdc_leads():
    return [
        {"Id": "00Q1", "Company": "CHRISTUS Health", "Email": "a@christushealth.org",
         "BN_Email_Domain__c": "christushealth.org", "LeadSource": "Sales Contact Form",
         "Status": "New", "CreatedDate": "2026-06-10T00:00:00.000+0000"},
        {"Id": "00Q2", "Company": "Newport Healthcare", "Email": "b@newporthealthcare.com",
         "BN_Email_Domain__c": "newporthealthcare.com", "LeadSource": "S+G Contact Form",
         "Status": "New", "CreatedDate": "2026-06-11T00:00:00.000+0000"},
        {"Id": "00Q3", "Company": "Random Co", "Email": "c@randomco.com",
         "BN_Email_Domain__c": "randomco.com", "LeadSource": "Sales Contact Form",
         "Status": "New", "CreatedDate": "2026-06-12T00:00:00.000+0000"},
    ]


def test_run_sfdc_sync_crosses_and_scores(tmp_path):
    repo = EngagementJsonRepository(path=str(tmp_path / "sf.json"))
    stats = sync_mod.run_sfdc_sync(
        engagement_repo=repo, scoring_repo=_FakeScoring(), discovery_repo=_FakeDiscovery(),
        client=_FakeSfdcClient(_sfdc_leads()), now="2026-06-14T00:00:00Z")

    assert stats["high_intent_leads"] == 3 and stats["contacts"] == 3
    assert stats["matched_contacts"] == 2 and stats["unresolved_contacts"] == 1

    accts = {a["account_id"]: a for a in repo.engaged_accounts()}
    assert accts["acc_christus"]["score"] == 10           # one high-intent lead x 10 (scored)
    assert accts["abm_newporthealthcare"]["score"] == 10  # one lead x 10 (ABM-only)
    assert not any(a.startswith("Random") for a in accts)  # unmatched -> not engaged
    assert repo.get_sync_state("sfdc")["status"] == "success"


def test_run_sfdc_sync_includes_tradeshow_meetings(tmp_path):
    repo = EngagementJsonRepository(path=str(tmp_path / "sft.json"))
    tradeshow = [
        {"Id": "00T1", "Company": "CHRISTUS Health", "Email": "t@christushealth.org",
         "BN_Email_Domain__c": "christushealth.org", "LeadSource": "Trade Show",
         "Status": "Qualified", "Tradeshow__c": "HIMSS 2026",
         "CreatedDate": "2026-03-01T00:00:00.000+0000"}]
    stats = sync_mod.run_sfdc_sync(
        engagement_repo=repo, scoring_repo=_FakeScoring(), discovery_repo=_FakeDiscovery(),
        client=_FakeSfdcClient(_sfdc_leads(), tradeshow=tradeshow), now="2026-06-14T00:00:00Z")

    assert stats["high_intent_leads"] == 3 and stats["tradeshow_leads"] == 1
    accts = {a["account_id"]: a for a in repo.engaged_accounts()}
    # CHRISTUS: 1 high-intent lead (10) + 1 tradeshow meeting (10) = 20
    assert accts["acc_christus"]["score"] == 20
    ts = next(e for e in repo.events_for_account("acc_christus") if e["kind"] == "tradeshow")
    assert ts["points"] == 10 and ts["channel"] == "event"
    assert ts["campaign"] == "HIMSS 2026"             # show name, not 'Trade Show'


def test_run_sfdc_sync_includes_low_intent_tofu(tmp_path):
    repo = EngagementJsonRepository(path=str(tmp_path / "sflo.json"))
    low = [{"Id": "00L1", "Company": "Newport Healthcare", "Email": "x@newporthealthcare.com",
            "BN_Email_Domain__c": "newporthealthcare.com", "LeadSource": "6 UM Trends 2026 | TOFU",
            "Status": "New", "CreatedDate": "2026-04-01T00:00:00.000+0000"}]
    stats = sync_mod.run_sfdc_sync(
        engagement_repo=repo, scoring_repo=_FakeScoring(), discovery_repo=_FakeDiscovery(),
        client=_FakeSfdcClient(_sfdc_leads(), low_intent=low), now="2026-06-14T00:00:00Z")
    assert stats["low_intent_leads"] == 1
    e = next(ev for ev in repo.events_for_account("abm_newporthealthcare") if ev["kind"] == "low_intent_lead")
    assert e["points"] == 6 and e["channel"] == "content"
    # Newport: 1 high-intent lead (10) + 1 TOFU form lead (6) = 16
    accts = {a["account_id"]: a for a in repo.engaged_accounts()}
    assert accts["abm_newporthealthcare"]["score"] == 16


def test_run_sfdc_sync_includes_meetings(tmp_path):
    repo = EngagementJsonRepository(path=str(tmp_path / "sfmtg.json"))
    meetings = [{"Id": "00U1", "Subject": "Intro call", "Type": "Meeting",
                 "AccountId": "001CH", "Account": {"Name": "CHRISTUS Health",
                                                   "Website": "https://www.christushealth.org/"},
                 "StartDateTime": "2026-05-01T00:00:00.000+0000",
                 "CreatedDate": "2026-05-01T00:00:00.000+0000"}]
    stats = sync_mod.run_sfdc_sync(
        engagement_repo=repo, scoring_repo=_FakeScoring(), discovery_repo=_FakeDiscovery(),
        client=_FakeSfdcClient(_sfdc_leads(), meetings=meetings), now="2026-06-14T00:00:00Z")
    assert stats["meetings"] == 1
    e = next(ev for ev in repo.events_for_account("acc_christus")
             if ev["kind"] == "meeting_booked")
    assert e["points"] == 10 and e["channel"] == "meeting"
    assert e["external_id"] == "meeting:meeting_booked:acct:001CH"
    # CHRISTUS: 1 high-intent lead (10) + 1 booked meeting (10) = 20
    accts = {a["account_id"]: a for a in repo.engaged_accounts()}
    assert accts["acc_christus"]["score"] == 20


def test_run_sfdc_sync_is_idempotent(tmp_path):
    repo = EngagementJsonRepository(path=str(tmp_path / "sf2.json"))
    # include a booked meeting so idempotency is proven across both id namespaces
    meetings = [{"Id": "00U1", "Subject": "Intro call", "Type": "Meeting",
                 "AccountId": "001CH", "Account": {"Name": "CHRISTUS Health",
                                                   "Website": "https://www.christushealth.org/"},
                 "StartDateTime": "2026-05-01T00:00:00.000+0000",
                 "CreatedDate": "2026-05-01T00:00:00.000+0000"}]
    first = sync_mod.run_sfdc_sync(
        engagement_repo=repo, scoring_repo=_FakeScoring(), discovery_repo=_FakeDiscovery(),
        client=_FakeSfdcClient(_sfdc_leads(), meetings=meetings), now="2026-06-14T00:00:00Z")
    second = sync_mod.run_sfdc_sync(
        engagement_repo=repo, scoring_repo=_FakeScoring(), discovery_repo=_FakeDiscovery(),
        client=_FakeSfdcClient(_sfdc_leads(), meetings=meetings), now="2026-06-14T00:00:00Z")
    # 2 matched leads + 1 meeting persist (unmatched aren't stored); re-sync adds nothing
    assert first["new_events"] == 3 and second["new_events"] == 0


@pytest.mark.asyncio
async def test_sfdc_replyio_podcast_coexist_on_same_account(tmp_path):
    repo = EngagementJsonRepository(path=str(tmp_path / "sf3.json"))
    # email reply (6) ...
    activity = [{"contactId": 1, "company": "CHRISTUS Health", "email": "g@christushealth.org",
                 "deliveryDate": "2026-06-05T00:00:00Z", "isDelivered": True, "isReplied": True}]
    await sync_mod.run_sync(
        engagement_repo=repo, scoring_repo=_FakeScoring(), discovery_repo=_FakeDiscovery(),
        client=_FakeClient([], activity), max_contacts=0, now="2026-06-14T00:00:00Z")
    # ... podcast lead (4) ...
    sync_mod.run_podcast_sync(
        engagement_repo=repo, scoring_repo=_FakeScoring(), discovery_repo=_FakeDiscovery(),
        rows=[{"Submit Date": "2026-02-01 00:00:00", "Work Email": "p@christushealth.org",
               "ICP": "Yes"}], now="2026-06-14T00:00:00Z")
    # ... plus an SFDC high-intent lead (10), all on CHRISTUS
    lead = [{"Id": "00Q9", "Company": "CHRISTUS Health", "Email": "h@christushealth.org",
             "BN_Email_Domain__c": "christushealth.org", "LeadSource": "Sales Contact Form",
             "Status": "New", "CreatedDate": "2026-06-10T00:00:00.000+0000"}]
    sync_mod.run_sfdc_sync(
        engagement_repo=repo, scoring_repo=_FakeScoring(), discovery_repo=_FakeDiscovery(),
        client=_FakeSfdcClient(lead), now="2026-06-14T00:00:00Z")
    accts = {a["account_id"]: a for a in repo.engaged_accounts()}
    assert accts["acc_christus"]["score"] == 20        # reply 6 + podcast 4 + lead 10, combined
