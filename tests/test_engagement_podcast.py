"""Podcast ingest — pure parse_rows + load_csv. No I/O."""

from __future__ import annotations

from auto_search.engagement import podcast


def _row(**kw):
    base = {"Submit Date": "2026-02-13 16:01:05", "First Name": "A", "Last Name": "B",
            "Work Email": "a@hospital.org", "ICP": "Yes", "Account Name": "",
            "Lead Form": "", "UTM_source": "", "UTM_campaign": ""}
    base.update(kw)
    return base


def test_keeps_yes_and_maybe_drops_others():
    rows = [_row(**{"Work Email": "yes@a.com", "ICP": "Yes"}),
            _row(**{"Work Email": "maybe@b.com", "ICP": "Maybe"}),
            _row(**{"Work Email": "no@c.com", "ICP": "No"}),
            _row(**{"Work Email": "blank@d.com", "ICP": ""}),
            _row(**{"Work Email": "junk@e.com", "ICP": "could not find"})]
    contacts, events = podcast.parse_rows(rows, now="2026-06-15T00:00:00+00:00")
    emails = {c["email"] for c in contacts}
    assert emails == {"yes@a.com", "maybe@b.com"}
    assert len(events) == 2


def test_handles_dashed_icp_values():
    # the sheet stores "- Yes " / "- Maybe" with a leading dash + whitespace
    rows = [_row(**{"Work Email": "x@a.com", "ICP": "- Yes "}),
            _row(**{"Work Email": "y@b.com", "ICP": "  - Maybe"})]
    contacts, _ = podcast.parse_rows(rows)
    assert {c["email"] for c in contacts} == {"x@a.com", "y@b.com"}


def test_event_shape_and_points():
    contacts, events = podcast.parse_rows(
        [_row(**{"Work Email": "Lead@Hospital.ORG", "Account Name": "Hospital Inc",
                 "Lead Form": "Podcast Vanessa", "UTM_source": "linkedin"})])
    e = events[0]
    assert e["source"] == "podcast"
    assert e["channel"] == "podcast"
    assert e["kind"] == "podcast_lead"
    assert e["points"] == 4
    assert e["external_id"] == "podcast:podcast_lead:lead@hospital.org"   # lowercased
    assert e["contact_ext"] == "lead@hospital.org"
    assert e["company"] == "Hospital Inc"
    assert e["campaign"] == "Podcast Vanessa"
    assert e["raw"]["icp"] == "Yes"
    assert e["raw"]["utm_source"] == "linkedin"
    assert e["occurred_at"].startswith("2026-02-13")
    c = contacts[0]
    assert c["email"] == "lead@hospital.org"
    assert c["email_domain"] == "hospital.org"
    assert c["company_key"]   # normalized non-empty


def test_dedup_prefers_yes_then_latest():
    rows = [_row(**{"Work Email": "dup@a.com", "ICP": "Maybe", "Submit Date": "2026-01-01 00:00:00"}),
            _row(**{"Work Email": "dup@a.com", "ICP": "Yes", "Submit Date": "2026-02-01 00:00:00"}),
            _row(**{"Work Email": "two@b.com", "ICP": "Yes", "Submit Date": "2026-01-01 00:00:00"}),
            _row(**{"Work Email": "two@b.com", "ICP": "Yes", "Submit Date": "2026-03-01 00:00:00"})]
    contacts, events = podcast.parse_rows(rows)
    assert len(events) == 2                     # one per unique email
    by = {e["contact_ext"]: e for e in events}
    assert by["dup@a.com"]["raw"]["icp"] == "Yes"            # Yes beat Maybe
    assert by["two@b.com"]["occurred_at"].startswith("2026-03-01")   # latest submit


def test_bad_or_missing_email_skipped():
    rows = [_row(**{"Work Email": "", "ICP": "Yes"}),
            _row(**{"Work Email": "notanemail", "ICP": "Yes"}),
            _row(**{"Work Email": "ok@a.com", "ICP": "Yes"})]
    contacts, _ = podcast.parse_rows(rows)
    assert [c["email"] for c in contacts] == ["ok@a.com"]


def test_blank_submit_date_falls_back_to_now():
    _, events = podcast.parse_rows(
        [_row(**{"Work Email": "a@a.com", "Submit Date": ""})],
        now="2026-06-15T00:00:00+00:00")
    assert events[0]["occurred_at"] == "2026-06-15T00:00:00+00:00"


def test_load_csv_roundtrip():
    text = ("Submit Date,First Name,Last Name,Work Email,ICP,Account Name,Lead Form,"
            "UTM_source,UTM_campaign\n"
            "2026-02-13 16:01:05,A,B,a@a.com,Yes,Acme,Podcast,linkedin,camp1\n"
            "2026-02-14 10:00:00,C,D,c@c.com,No,,,\n")
    rows = podcast.load_csv(text)
    contacts, events = podcast.parse_rows(rows)
    assert [c["email"] for c in contacts] == ["a@a.com"]   # only the Yes row
    assert events[0]["campaign"] == "Podcast"
