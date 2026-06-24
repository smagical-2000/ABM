"""Engagement storage (Milestone A) — idempotency, the engaged-accounts rollup,
the unresolved queue, and sync state.

Runs on the JSON repo (no Postgres needed, like the rest of the unit suite). A
guarded Postgres round-trip mirrors test_postgres_repository.py and is opt-in
(set ENGAGEMENT_PG_TEST=1 with a local DATABASE_URL) so it never touches a real
database in normal CI.

Event external_id convention: "<channel>:<kind>:<contactId>" — source is its own
column, so it is not repeated in the id.
"""

import os

import pytest

from auto_search.db.engagement_repository import EngagementJsonRepository


def _repo(tmp_path):
    return EngagementJsonRepository(path=str(tmp_path / "eng.json"))


def _event(ext, kind, points, *, account_id="acc_x", contact="1",
           at="2026-06-10T00:00:00+00:00"):
    return {"external_id": ext, "channel": "email", "kind": kind, "points": points,
            "contact_ext": contact, "account_id": account_id, "occurred_at": at}


def test_deprecated_sao_excluded_from_heat_and_drawer(tmp_path):
    """Retired SAO events stay in storage (audit) but must NOT count toward heat,
    appear in the drawer (events_for_account), the inbox (recent_events), or momentum
    (account_weekly_series). Guards the 2026-06 SAO retirement."""
    repo = _repo(tmp_path)
    repo.add_event(_event("email:reply:1", "reply", 6))
    repo.add_event(_event("crm:sales_accepted_opportunity:acc_x",
                          "sales_accepted_opportunity", 10))

    acct = {a["account_id"]: a for a in repo.engaged_accounts()}["acc_x"]
    assert acct["score"] == 6           # reply 6 only — SAO's 10 excluded
    kinds = {e["kind"] for e in repo.events_for_account("acc_x")}
    assert "sales_accepted_opportunity" not in kinds and "reply" in kinds
    assert all(e["kind"] != "sales_accepted_opportunity" for e in repo.recent_events())
    series = repo.account_weekly_series()
    assert sum(series.get("acc_x", [])) == 6     # momentum excludes SAO too


def test_add_event_is_idempotent(tmp_path):
    repo = _repo(tmp_path)
    ev = _event("email:reply:1", "reply", 6)
    assert repo.add_event(ev) is True          # first time inserts
    assert repo.add_event(ev) is False         # re-sync upserts, never duplicates
    assert len(repo.events_for_account("acc_x")) == 1


def test_add_event_survives_reload(tmp_path):
    path = tmp_path / "eng.json"
    EngagementJsonRepository(path=str(path)).add_event(_event("email:reply:1", "reply", 6))
    # a fresh instance reads the same file (mirrors a process restart)
    assert len(EngagementJsonRepository(path=str(path)).events_for_account("acc_x")) == 1


def test_upsert_contact_idempotent_and_updates(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_contact({"external_id": "1", "email": "a@acme.com",
                         "email_domain": "acme.com", "clicked": 2})
    repo.upsert_contact({"external_id": "1", "email": "a@acme.com", "email_domain": "acme.com",
                         "clicked": 3, "account_id": "acc_x", "match_tier": "domain",
                         "matched_lists": ["scored", "abm"]})
    rows = repo.contacts()
    assert len(rows) == 1
    assert rows[0]["clicked"] == 3
    assert rows[0]["account_id"] == "acc_x"
    assert rows[0]["matched_lists"] == ["scored", "abm"]


def test_engaged_accounts_rollup(tmp_path):
    repo = _repo(tmp_path)
    # one contact at acc_x that clicked, replied, and booked a meeting
    repo.upsert_contact({"external_id": "1", "account_id": "acc_x",
                         "delivered": 10, "opened": 4, "replied": 1})
    repo.add_event(_event("email:click:1", "click", 1, at="2026-06-01T00:00:00+00:00"))
    repo.add_event(_event("email:reply:1", "reply", 6, at="2026-06-05T00:00:00+00:00"))
    repo.add_event(_event("email:meeting_booked:1", "meeting_booked", 10,
                          at="2026-06-06T00:00:00+00:00"))
    rows = {r["account_id"]: r for r in repo.engaged_accounts()}
    a = rows["acc_x"]
    assert a["score"] == 17                     # 1 + 6 + 10
    assert (a["clicks"], a["replies"], a["meetings"]) == (1, 1, 1)
    assert a["contacts"] == 1
    assert a["delivered"] == 10 and a["opened"] == 4
    assert a["last_touch"] == "2026-06-06T00:00:00+00:00"


def test_engaged_account_with_contact_only_has_rates_but_zero_score(tmp_path):
    """A matched contact with deliveries/opens but no click/reply/meeting still
    appears (rates inputs present, score 0) — so the account isn't invisible."""
    repo = _repo(tmp_path)
    repo.upsert_contact({"external_id": "9", "account_id": "acc_quiet",
                         "delivered": 7, "opened": 3})
    rows = {r["account_id"]: r for r in repo.engaged_accounts()}
    a = rows["acc_quiet"]
    assert a["score"] == 0
    assert a["contacts"] == 1
    assert a["delivered"] == 7 and a["opened"] == 3
    assert a["last_touch"] is None


def test_engaged_accounts_ranked_by_score_desc(tmp_path):
    repo = _repo(tmp_path)
    repo.add_event(_event("email:reply:1", "reply", 6, account_id="low"))
    repo.add_event(_event("email:meeting_booked:2", "meeting_booked", 10, account_id="high",
                          contact="2"))
    assert [r["account_id"] for r in repo.engaged_accounts()] == ["high", "low"]


def test_score_does_not_inflate_on_repeat_sends(tmp_path):
    """Re-syncing the same contact's reply many times keeps the score at +6 once —
    the (source, external_id) primary key enforces one row per contact x kind."""
    repo = _repo(tmp_path)
    repo.upsert_contact({"external_id": "1", "account_id": "acc_x"})
    for _ in range(5):
        repo.add_event(_event("email:reply:1", "reply", 6))
    rows = {r["account_id"]: r for r in repo.engaged_accounts()}
    assert rows["acc_x"]["score"] == 6
    assert rows["acc_x"]["replies"] == 1


def test_unresolved_contacts_filter(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_contact({"external_id": "1", "account_id": "acc_x"})
    repo.upsert_contact({"external_id": "2"})   # no account -> unresolved
    assert {c["external_id"] for c in repo.contacts(unresolved_only=True)} == {"2"}


def test_account_weekly_series_buckets_by_week(tmp_path):
    from datetime import UTC, datetime, timedelta
    repo = _repo(tmp_path)
    now = datetime.now(UTC)
    this_week = now.isoformat()
    three_weeks = (now - timedelta(weeks=3)).isoformat()
    repo.add_event(_event("email:click:1", "click", 1, at=this_week))
    repo.add_event(_event("form:high_intent_lead:2", "high_intent_lead", 10, at=this_week))
    repo.add_event(_event("podcast:podcast_lead:3", "podcast_lead", 4, at=three_weeks))
    repo.add_event(_event("email:click:9", "click", 1, account_id=None, at=this_week))  # unmatched
    series = repo.account_weekly_series(weeks=8)
    s = series["acc_x"]
    assert len(s) == 8
    assert s[-1] == 11        # current week: click 1 + lead 10
    assert s[-4] == 4         # 3 weeks ago: podcast 4
    assert None not in series  # unmatched (account_id None) is excluded


def test_recent_events_newest_first(tmp_path):
    repo = _repo(tmp_path)
    repo.add_event(_event("email:click:1", "click", 1, at="2026-06-01T00:00:00+00:00"))
    repo.add_event(_event("email:reply:1", "reply", 6, at="2026-06-09T00:00:00+00:00"))
    assert [e["external_id"] for e in repo.recent_events(limit=10)] == ["email:reply:1",
                                                                        "email:click:1"]


def test_sync_state_roundtrip_and_partial_update(tmp_path):
    repo = _repo(tmp_path)
    assert repo.get_sync_state() is None
    repo.set_sync_state(status="running", window_from="2026-05-15", window_to="2026-06-14")
    repo.set_sync_state(status="success", stats={"events": 3})   # partial: window kept
    s = repo.get_sync_state()
    assert s["status"] == "success"
    assert s["stats"]["events"] == 3
    assert s["window_from"] == "2026-05-15"      # not clobbered by the partial update
    assert s["error"] is None                     # success cleared it
    assert s["last_synced_at"]


def test_delete_all(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_contact({"external_id": "1", "account_id": "acc_x"})
    repo.add_event(_event("email:reply:1", "reply", 6))
    assert repo.delete_all() >= 1
    assert repo.engaged_accounts() == []
    assert repo.contacts() == []


# ── guarded Postgres round-trip (opt-in) ──────────────────────────────


@pytest.mark.skipif(
    not (os.getenv("ENGAGEMENT_PG_TEST") and os.getenv("DATABASE_URL")),
    reason="set ENGAGEMENT_PG_TEST=1 + DATABASE_URL to run the Postgres round-trip",
)
def test_postgres_roundtrip():
    from auto_search.db.engagement_repository import EngagementPostgresRepository
    repo = EngagementPostgresRepository()
    repo.ensure_schema()
    repo.delete_all()
    try:
        repo.upsert_contact({"external_id": "t1", "account_id": "acc_pg",
                             "delivered": 5, "opened": 2})
        assert repo.add_event(_event("email:reply:t1", "reply", 6,
                                     account_id="acc_pg", contact="t1")) is True
        assert repo.add_event(_event("email:reply:t1", "reply", 6,
                                     account_id="acc_pg", contact="t1")) is False
        rows = {r["account_id"]: r for r in repo.engaged_accounts()}
        assert rows["acc_pg"]["score"] == 6
        assert rows["acc_pg"]["delivered"] == 5
        repo.set_sync_state(status="success", window_from="2026-05-15", window_to="2026-06-14")
        assert repo.get_sync_state()["status"] == "success"
    finally:
        repo.delete_all()
        repo.close()
