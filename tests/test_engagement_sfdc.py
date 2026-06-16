"""SFDC ingest — pure parse(). No I/O."""

from __future__ import annotations

from auto_search.engagement import sfdc


def _meeting(**kw):
    base = {"Id": "00U1", "Subject": "Acme <> Magical Introduction", "Type": "Meeting",
            "AccountId": "001ACME", "Account": {"Name": "Acme Health",
                                                "Website": "https://www.acme.com/"},
            "WhoId": "003X", "Who": {"Name": "Jo", "Type": "Contact"},
            "StartDateTime": "2026-06-20T17:00:00.000+0000",
            "CreatedDate": "2026-06-10T00:00:00.000+0000"}
    base.update(kw)
    return base


def _opp(**kw):
    base = {"Id": "0061", "Name": "Acme Deal", "StageName": "Qualification",
            "IsClosed": False, "IsWon": False, "Amount": 50000.0,
            "AccountId": "001ACME", "Account": {"Name": "Acme Health",
                                                "Website": "https://www.acme.com/"},
            "CreatedDate": "2026-06-05T00:00:00.000+0000", "CloseDate": "2026-09-01"}
    base.update(kw)
    return base


def test_meeting_event_shape_and_points():
    contacts, events = sfdc.parse([_meeting()], [], now="2026-06-15T00:00:00+00:00")
    assert len(contacts) == 1 and len(events) == 1
    e = events[0]
    assert e["source"] == "sfdc"
    assert e["channel"] == "meeting"
    assert e["kind"] == "meeting_booked"
    assert e["points"] == 10
    assert e["external_id"] == "meeting:meeting_booked:acct:001ACME"
    assert e["contact_ext"] == "acct:001ACME"
    assert e["company"] == "Acme Health"
    assert e["occurred_at"].startswith("2026-06-20")        # StartDateTime, not Created
    c = contacts[0]
    assert c["external_id"] == "acct:001ACME"
    assert c["email_domain"] == "acme.com"                   # from Account.Website
    assert c["company_key"]                                  # normalized non-empty
    assert c["meeting_booked"] is True


def test_opportunity_event_shape_and_points():
    _, events = sfdc.parse([], [_opp()], now="2026-06-15T00:00:00+00:00")
    e = events[0]
    assert e["channel"] == "crm"
    assert e["kind"] == "opportunity"
    assert e["points"] == 10
    assert e["external_id"] == "crm:opportunity:acct:001ACME"
    assert e["raw"]["stage"] == ["Qualification"]
    assert e["raw"]["count"] == 1


def test_meeting_and_opp_on_same_account_dedup_to_two_events():
    # two meetings + one opp on ONE account -> 1 contact, 2 events (not 3),
    # meeting scores 10 once (not 20) — the one-touch-per-account×kind rule.
    meetings = [_meeting(Id="00U1", StartDateTime="2026-06-20T00:00:00.000+0000"),
                _meeting(Id="00U2", StartDateTime="2026-06-25T00:00:00.000+0000")]
    contacts, events = sfdc.parse(meetings, [_opp()])
    assert len(contacts) == 1
    assert len(events) == 2
    by = {e["kind"]: e for e in events}
    assert by["meeting_booked"]["points"] == 10
    assert by["meeting_booked"]["raw"]["count"] == 2                    # audit trail
    assert sorted(by["meeting_booked"]["raw"]["ids"]) == ["00U1", "00U2"]
    assert by["meeting_booked"]["occurred_at"].startswith("2026-06-25")  # most recent


def test_accountless_meeting_falls_back_to_subject_company():
    m = _meeting(Id="00U9", AccountId=None, Account=None,
                 Subject="Yosemite Medical Clinic <> Magical Introduction",
                 Who={"Name": "Michelle", "Type": "Lead"})
    contacts, events = sfdc.parse([m], [])
    assert len(contacts) == 1
    c = contacts[0]
    assert c["external_id"].startswith("name:")             # keyed by normalized name
    assert c["company"] == "Yosemite Medical Clinic"
    assert c["email_domain"] is None                        # no website to derive from
    assert events[0]["contact_ext"] == c["external_id"]


def test_meeting_with_no_account_and_no_subject_company_skipped():
    m = _meeting(Id="00U0", AccountId=None, Account=None, Subject="Sync")
    contacts, events = sfdc.parse([m], [])
    assert contacts == [] and events == []


def test_won_opportunity_carried_in_raw():
    _, events = sfdc.parse([], [_opp(IsWon=True, IsClosed=True, StageName="Closed Won")])
    assert events[0]["raw"]["is_won"] == [True]
    assert events[0]["raw"]["stage"] == ["Closed Won"]


def test_two_distinct_accounts_make_two_contacts():
    a = _meeting(Id="00U1", AccountId="001A", Account={"Name": "A Co", "Website": "a.com"})
    b = _opp(Id="0062", AccountId="001B", Account={"Name": "B Co", "Website": "b.com"})
    contacts, events = sfdc.parse([a], [b])
    assert {c["external_id"] for c in contacts} == {"acct:001A", "acct:001B"}
    assert len(events) == 2


# ── high-intent leads (the active sync path) ────────────────────────────


def _lead(**kw):
    base = {"Id": "00Q1", "FirstName": "Jo", "LastName": "Doe", "Company": "Acme Health",
            "Email": "jo@acme.com", "BN_Email_Domain__c": "acme.com", "Website": "acme.com",
            "Title": "VP Ops", "LeadSource": "Sales Contact Form", "Status": "New",
            "MQL__c": True, "Seats_Requested__c": "50", "In_Healthcare__c": "Yes",
            "Primary_Purpose__c": "Text Expansion", "Employee_Range__c": "201-500",
            "IsConverted": False, "CreatedDate": "2026-06-10T00:00:00.000+0000"}
    base.update(kw)
    return base


def test_lead_event_shape_and_points():
    contacts, events = sfdc.parse_leads([_lead()], now="2026-06-15T00:00:00+00:00")
    assert len(contacts) == 1 and len(events) == 1
    e = events[0]
    assert e["source"] == "sfdc"
    assert e["channel"] == "form"
    assert e["kind"] == "high_intent_lead"
    assert e["points"] == 10
    assert e["external_id"] == "form:high_intent_lead:00Q1"
    assert e["contact_ext"] == "00Q1"
    assert e["company"] == "Acme Health"
    assert e["campaign"] == "Sales Contact Form"          # carries the source
    assert e["raw"]["mql"] is True
    assert e["raw"]["status"] == "New"
    assert e["occurred_at"].startswith("2026-06-10")
    c = contacts[0]
    assert c["external_id"] == "00Q1"
    assert c["email"] == "jo@acme.com"
    assert c["email_domain"] == "acme.com"                # from BN_Email_Domain__c
    assert c["company_key"]


def test_lead_domain_falls_back_to_email_then_website():
    # no BN_Email_Domain__c -> derive from email
    c1, _ = sfdc.parse_leads([_lead(BN_Email_Domain__c=None, Email="x@foo.com")])
    assert c1[0]["email_domain"] == "foo.com"
    # no domain + no email -> derive from website
    c2, _ = sfdc.parse_leads([_lead(BN_Email_Domain__c=None, Email=None,
                                    Website="https://www.bar.com/")])
    assert c2[0]["email_domain"] == "bar.com"


def test_leads_deduped_by_id():
    contacts, events = sfdc.parse_leads([_lead(Id="00Q1"), _lead(Id="00Q1"),
                                         _lead(Id="00Q2")])
    assert {c["external_id"] for c in contacts} == {"00Q1", "00Q2"}
    assert len(events) == 2


def test_lead_missing_id_skipped():
    contacts, events = sfdc.parse_leads([_lead(Id=None), _lead(Id="00Q2")])
    assert [c["external_id"] for c in contacts] == ["00Q2"]


def test_two_leads_same_company_both_count():
    # two people from one company -> two BOFU signals (per-contact, not deduped)
    leads = [_lead(Id="00Q1", Email="a@acme.com"), _lead(Id="00Q2", Email="b@acme.com")]
    contacts, events = sfdc.parse_leads(leads)
    assert len(contacts) == 2 and len(events) == 2
    assert all(e["points"] == 10 for e in events)
