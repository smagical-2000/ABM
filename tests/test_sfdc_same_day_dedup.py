"""SFDC same-person form-lead dedup (MAR2-50 B, the Ascension double-count).

One human submitted the form twice in 6 minutes; SFDC minted two Lead ids, so
two event external_ids landed and the account scored +20 instead of +10.
Form-lead events must dedup by (person, kind, occurred date): a same-day
resubmit is idempotent, collapsing to the OLDEST lead id (stable across
re-pulls — an insert guard, not a migration; already-stored events are
preserved). A re-engagement WEEKS later is a new date and still counts.
"""

from __future__ import annotations

from auto_search.db.engagement_repository import EngagementJsonRepository
from auto_search.engagement import sfdc
from auto_search.engagement import sync as sync_mod

NOW = "2026-07-28T12:00:00+00:00"


def _lead(lid, created, email="pat@ascension.org", first="Pat", last="Ash",
          company="Ascension"):
    return {"Id": lid, "FirstName": first, "LastName": last, "Company": company,
            "Email": email, "BN_Email_Domain__c": email.rsplit("@", 1)[-1],
            "LeadSource": "Sales Contact Form", "Status": "New",
            "CreatedDate": created}


def test_two_same_day_leads_from_one_email_collapse_to_one_event():
    contacts, events = sfdc.parse_leads(
        [_lead("00Q1", "2026-07-27T09:00:00.000+0000"),
         _lead("00Q2", "2026-07-27T09:06:00.000+0000")], now=NOW)
    assert len(events) == 1 and len(contacts) == 1
    # oldest lead id is the canonical, stable external_id
    assert events[0]["external_id"] == "form:high_intent_lead:00Q1"
    assert contacts[0]["external_id"] == "00Q1"


def test_reengagement_weeks_later_still_counts():
    contacts, events = sfdc.parse_leads(
        [_lead("00Q1", "2026-06-27T09:00:00.000+0000"),
         _lead("00Q9", "2026-07-27T10:00:00.000+0000")], now=NOW)
    assert len(events) == 2
    assert {e["external_id"] for e in events} == {
        "form:high_intent_lead:00Q1", "form:high_intent_lead:00Q9"}


def test_collapse_keys_on_person_even_without_email():
    """No email -> normalized name+company keys the person (echo-filter rule)."""
    a = _lead("00Q1", "2026-07-27T09:00:00.000+0000")
    b = _lead("00Q2", "2026-07-27T09:06:00.000+0000")
    a["Email"] = b["Email"] = ""
    a.pop("BN_Email_Domain__c"), b.pop("BN_Email_Domain__c")
    _, events = sfdc.parse_leads([a, b], now=NOW)
    assert len(events) == 1
    assert events[0]["external_id"] == "form:high_intent_lead:00Q1"


def test_different_people_same_day_both_count():
    _, events = sfdc.parse_leads(
        [_lead("00Q1", "2026-07-27T09:00:00.000+0000"),
         _lead("00Q2", "2026-07-27T09:06:00.000+0000", email="lee@ascension.org",
               first="Lee", last="Bond")], now=NOW)
    assert len(events) == 2


def test_order_independent_oldest_wins():
    """SOQL row order is not guaranteed — the newer-first ordering must still
    collapse to the oldest lead id."""
    _, events = sfdc.parse_leads(
        [_lead("00Q2", "2026-07-27T09:06:00.000+0000"),
         _lead("00Q1", "2026-07-27T09:00:00.000+0000")], now=NOW)
    assert len(events) == 1
    assert events[0]["external_id"] == "form:high_intent_lead:00Q1"


# ── end-to-end through the sync (score is +10, not +20) ─────────────────


class _Scoring:
    def list_accounts(self):
        return [{"account_id": "acc_ascension", "name": "Ascension",
                 "domain": "ascension.org"}]


class _Discovery:
    def abm_targets(self):
        return []


class _SfdcClient:
    def __init__(self, leads):
        self._leads = leads

    def iter_high_intent_leads(self, *, since="2026-01-01"):
        yield from self._leads

    def iter_tradeshow_leads(self, *, since="2026-01-01"):
        yield from []

    def iter_low_intent_leads(self, *, since="2026-01-01"):
        yield from []

    def iter_meetings(self, *, days=180):
        yield from []


def test_double_submit_scores_ten_not_twenty(tmp_path):
    repo = EngagementJsonRepository(path=str(tmp_path / "e.json"))
    sync_mod.run_sfdc_sync(
        engagement_repo=repo, scoring_repo=_Scoring(), discovery_repo=_Discovery(),
        client=_SfdcClient([_lead("00Q1", "2026-07-27T09:00:00.000+0000"),
                            _lead("00Q2", "2026-07-27T09:06:00.000+0000")]),
        now=NOW)
    accts = {a["account_id"]: a for a in repo.engaged_accounts()}
    assert accts["acc_ascension"]["score"] == 10
