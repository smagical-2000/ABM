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
