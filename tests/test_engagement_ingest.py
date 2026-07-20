"""Ingest (Milestone C) — Reply.io rows -> normalized contacts + events."""

from auto_search.engagement.ingest import ingest


def test_aggregates_counts_and_emits_meaningful_events():
    contacts = [{"id": 1, "email": "gloria@christushealth.org", "domain": "christushealth.org",
                 "company": "CHRISTUS Health", "title": "RC Manager",
                 "meetingStatus": "meetingBooked"}]
    activity = [
        {"contactId": 1, "company": "CHRISTUS Health", "email": "gloria@christushealth.org",
         "sequenceName": "Q2 Health Systems", "deliveryDate": "2026-06-05T00:00:00Z",
         "isDelivered": True, "isOpened": True, "isClicked": True, "isReplied": False,
         "isBounced": False},
        {"contactId": 1, "company": "CHRISTUS Health", "email": "gloria@christushealth.org",
         "sequenceName": "Q2 Health Systems", "deliveryDate": "2026-06-09T00:00:00Z",
         "isDelivered": True, "isOpened": True, "isClicked": False, "isReplied": True,
         "isBounced": False},
    ]
    crows, erows = ingest(contacts, activity, now="2026-06-14T00:00:00Z")

    c = crows[0]
    assert c["external_id"] == "1"
    assert c["company_key"] == "christushealth"
    assert c["email_domain"] == "christushealth.org"
    assert (c["sent"], c["delivered"], c["opened"], c["clicked"], c["replied"]) == (2, 2, 2, 1, 1)
    assert c["meeting_booked"] is True

    events = {e["kind"]: e for e in erows}
    assert set(events) == {"click", "reply", "meeting_booked"}
    assert events["click"]["points"] == 1
    assert events["reply"]["points"] == 6
    assert events["meeting_booked"]["points"] == 10
    assert events["click"]["external_id"] == "email:click:1"
    assert events["click"]["occurred_at"] == "2026-06-05T00:00:00Z"     # latest click date
    assert events["reply"]["occurred_at"] == "2026-06-09T00:00:00Z"     # latest reply date
    assert events["meeting_booked"]["occurred_at"] == "2026-06-09T00:00:00Z"  # latest activity


def test_contact_only_in_activity_still_produced():
    activity = [{"contactId": 42, "company": "Acme Health", "email": "x@acme.com",
                 "deliveryDate": "2026-06-01T00:00:00Z", "isDelivered": True, "isClicked": True}]
    crows, erows = ingest([], activity, now="2026-06-14T00:00:00Z")
    assert crows[0]["external_id"] == "42"
    assert crows[0]["company_key"] == "acmehealth"
    assert crows[0]["meeting_booked"] is False        # no contact roster -> no meeting status
    assert {e["kind"] for e in erows} == {"click"}


def test_no_meaningful_touch_means_no_events():
    activity = [{"contactId": 7, "company": "Quiet Co", "email": "q@quiet.com",
                 "deliveryDate": "2026-06-01T00:00:00Z", "isDelivered": True, "isOpened": True}]
    crows, erows = ingest([], activity, now="2026-06-14T00:00:00Z")
    assert crows[0]["delivered"] == 1 and crows[0]["opened"] == 1
    assert erows == []                                # delivered/opened are not events


def test_meeting_only_uses_now_fallback_for_date():
    contacts = [{"id": 5, "email": "m@meet.com", "company": "Meet Co",
                 "meetingStatus": "meetingBooked"}]
    crows, erows = ingest(contacts, [], now="2026-06-14T00:00:00Z")
    assert [e["kind"] for e in erows] == ["meeting_booked"]
    assert erows[0]["occurred_at"] == "2026-06-14T00:00:00Z"


def test_email_domain_falls_back_to_contact_domain():
    # meeting makes the contact part of the engaged universe even with no email
    contacts = [{"id": 9, "domain": "fallback.com", "company": "Fb",
                 "meetingStatus": "meetingBooked"}]
    crows, _ = ingest(contacts, [], now="2026-06-14T00:00:00Z")
    assert crows[0]["email_domain"] == "fallback.com"


def test_roster_contact_with_no_engagement_is_skipped():
    """A roster contact with no activity in the window and no booked meeting is
    NOT engaged — it must not create a zero-engagement account row."""
    contacts = [{"id": 100, "email": "nobody@x.com", "company": "X", "meetingStatus": "none"}]
    crows, erows = ingest(contacts, [], now="2026-06-14T00:00:00Z")
    assert crows == [] and erows == []


def test_idempotent_external_ids_are_stable():
    activity = [{"contactId": 1, "deliveryDate": "2026-06-01T00:00:00Z",
                 "isDelivered": True, "isReplied": True}]
    _, e1 = ingest([], activity, now="2026-06-14T00:00:00Z")
    _, e2 = ingest([], activity, now="2026-06-14T00:00:00Z")
    assert [e["external_id"] for e in e1] == [e["external_id"] for e in e2] == ["email:reply:1"]
