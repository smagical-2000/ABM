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
        client=_FakeClient(contacts, activity), days=30, max_contacts=None,
        now="2026-06-14T00:00:00Z")

    assert stats["activity_rows"] == 3
    assert stats["matched_contacts"] == 2 and stats["unresolved_contacts"] == 1

    accts = {a["account_id"]: a for a in repo.engaged_accounts()}
    assert accts["acc_christus"]["score"] == 17        # click 1 + reply 6 + meeting 10 (scored)
    assert accts["abm_newporthealthcare"]["score"] == 6  # reply 6 (ABM-only)
    assert not any(a.startswith("Random") for a in accts)  # unmatched -> not engaged

    assert repo.get_sync_state()["status"] == "success"


@pytest.mark.asyncio
async def test_run_sync_widens_to_60_days_when_sparse(tmp_path):
    repo = EngagementJsonRepository(path=str(tmp_path / "e2.json"))
    calls = []

    class _SparseClient:
        async def iter_contacts(self, *, top=1000):
            return
            yield  # pragma: no cover (never reached — max_contacts=0 skips the roster)

        async def iter_email_activity(self, *, date_from, date_to, top=200):
            calls.append(date_from)
            if len(calls) == 1:                         # first (30d) window: sparse
                yield {"contactId": 1, "isDelivered": True, "deliveryDate": "2026-06-10T00:00:00Z"}
            else:                                       # widened (60d) window: enough rows
                for i in range(25):
                    yield {"contactId": i, "company": f"Co{i}", "isDelivered": True,
                           "isClicked": True, "deliveryDate": "2026-05-10T00:00:00Z"}

    stats = await sync_mod.run_sync(
        engagement_repo=repo, scoring_repo=_FakeScoring(), discovery_repo=_FakeDiscovery(),
        client=_SparseClient(), max_contacts=0, now="2026-06-14T00:00:00Z")
    assert len(calls) == 2 and stats["window_days"] == 60


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
    assert first["new_events"] == 4 and second["new_events"] == 0   # re-sync adds nothing
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


# ── sfdc sync (same source-agnostic cross + store path) ─────────────────


class _FakeSfdcClient:
    def __init__(self, leads):
        self._leads = leads

    def iter_high_intent_leads(self, *, days=365):
        yield from self._leads


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

    assert stats["leads"] == 3 and stats["contacts"] == 3
    assert stats["matched_contacts"] == 2 and stats["unresolved_contacts"] == 1

    accts = {a["account_id"]: a for a in repo.engaged_accounts()}
    assert accts["acc_christus"]["score"] == 10           # one high-intent lead x 10 (scored)
    assert accts["abm_newporthealthcare"]["score"] == 10  # one lead x 10 (ABM-only)
    assert not any(a.startswith("Random") for a in accts)  # unmatched -> not engaged
    assert repo.get_sync_state("sfdc")["status"] == "success"


def test_run_sfdc_sync_is_idempotent(tmp_path):
    repo = EngagementJsonRepository(path=str(tmp_path / "sf2.json"))
    first = sync_mod.run_sfdc_sync(
        engagement_repo=repo, scoring_repo=_FakeScoring(), discovery_repo=_FakeDiscovery(),
        client=_FakeSfdcClient(_sfdc_leads()), now="2026-06-14T00:00:00Z")
    second = sync_mod.run_sfdc_sync(
        engagement_repo=repo, scoring_repo=_FakeScoring(), discovery_repo=_FakeDiscovery(),
        client=_FakeSfdcClient(_sfdc_leads()), now="2026-06-14T00:00:00Z")
    # only the 2 matched leads persist (unmatched aren't stored); re-sync adds nothing
    assert first["new_events"] == 2 and second["new_events"] == 0


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
