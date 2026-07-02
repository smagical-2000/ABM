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

from auto_search.db.engagement_repository import (
    EngagementJsonRepository,
    contact_person_key,
    dedupe_contacts,
    engaging_contacts,
)


def _repo(tmp_path):
    return EngagementJsonRepository(path=str(tmp_path / "eng.json"))


def _event(ext, kind, points, *, account_id="acc_x", contact="1",
           at="2026-06-10T00:00:00+00:00"):
    return {"external_id": ext, "channel": "email", "kind": kind, "points": points,
            "contact_ext": contact, "account_id": account_id, "occurred_at": at}


def test_claim_activation_is_atomic_and_releasable(tmp_path):
    """Server-side activation dedup: the first claim wins, a second loses (so two reps
    fire it once); release undoes a claim so a failed post can retry."""
    repo = _repo(tmp_path)
    assert repo.claim_activation("acc_x") is True      # first rep wins
    assert repo.claim_activation("acc_x") is False     # second rep loses (already activated)
    assert repo.is_activated("acc_x") is True
    repo.release_activation("acc_x")                   # e.g. the Slack post failed
    assert repo.is_activated("acc_x") is False
    assert repo.claim_activation("acc_x") is True      # can re-claim after release


def test_activated_account_ids_and_reset(tmp_path):
    """The board reads activated_account_ids() to badge actioned accounts; reset clears
    the ledger so the SDR/AE testing phase can re-activate everything."""
    repo = _repo(tmp_path)
    repo.claim_activation("acc_a")
    repo.claim_activation("acc_b")
    assert repo.activated_account_ids() == {"acc_a", "acc_b"}
    assert repo.reset_activations() == 2
    assert repo.activated_account_ids() == set()
    assert repo.claim_activation("acc_a") is True   # can re-activate after reset


def test_setting_roundtrip_and_survives_reload(tmp_path):
    """The live-routing toggle (and any runtime setting) persists across restarts so
    the console button is the source of truth, not an env var."""
    path = tmp_path / "eng.json"
    repo = EngagementJsonRepository(path=str(path))
    assert repo.get_setting("live_routing") is None      # unset → None (falls back to env)
    repo.set_setting("live_routing", "1")
    assert repo.get_setting("live_routing") == "1"
    repo.set_setting("live_routing", "0")                 # upsert overwrites
    assert EngagementJsonRepository(path=str(path)).get_setting("live_routing") == "0"


def test_future_occurred_at_is_clamped_to_now(tmp_path):
    """A meeting scheduled in the future must never be stored as a future date — it would
    inflate today's heat, show a future timeline entry, and trip the send cutoff."""
    from datetime import UTC, datetime
    repo = _repo(tmp_path)
    repo.add_event(_event("crm:meeting_booked:acc_x", "meeting_booked", 10,
                          at="2999-01-01T00:00:00+00:00"))
    ev = repo.events_for_account("acc_x")[0]
    assert not ev["occurred_at"].startswith("2999")                       # clamped
    assert ev["occurred_at"][:10] <= datetime.now(UTC).date().isoformat()  # not in the future


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


def test_engaged_accounts_dedupes_same_person_across_sources(tmp_path):
    """The Central Ohio case: one human as an SFDC lead AND a Reply.io contact (same
    email, different external_id, even different case) is ONE person — counted once (they
    engaged via an open), and send-stats still sum across both rows."""
    repo = _repo(tmp_path)
    repo.upsert_contact({"external_id": "sfdc-1", "source": "sfdc",
                         "account_id": "acc_x", "email": "s.ridge@copcp.com"})
    repo.upsert_contact({"external_id": "reply-1", "source": "replyio",
                         "account_id": "acc_x", "email": "S.Ridge@copcp.com",  # same, diff case
                         "sent": 1, "delivered": 1, "opened": 1})              # engaged (opened)
    a = {r["account_id"]: r for r in repo.engaged_accounts()}["acc_x"]
    assert a["contacts"] == 1                 # one person, not two source-rows
    assert a["delivered"] == 1 and a["opened"] == 1


def test_engaged_count_excludes_silent_recipients_includes_opens_meetings_events(tmp_path):
    """"contacts engaging" = real engagements: an opener, a booked meeting (even with no
    email), and a scored-event person — but NOT a delivered-but-never-opened recipient."""
    repo = _repo(tmp_path)
    repo.upsert_contact({"external_id": "op", "account_id": "acc_e",       # opener → counts
                         "email": "opener@x.com", "delivered": 3, "opened": 2})
    repo.upsert_contact({"external_id": "silent", "account_id": "acc_e",   # never opened → no
                         "email": "silent@x.com", "delivered": 4, "opened": 0})
    repo.upsert_contact({"external_id": "acct:001", "source": "sfdc",      # meeting, no email → counts
                         "account_id": "acc_e", "meeting_booked": True})
    repo.upsert_contact({"external_id": "linkedin:pid", "account_id": "acc_e",  # scored event → counts
                         "email": "reactor@x.com"})
    repo.add_event({"external_id": "linkedin:linkedin_tofu:pid", "channel": "linkedin",
                    "kind": "linkedin_tofu", "points": 6, "contact_ext": "linkedin:pid",
                    "account_id": "acc_e", "occurred_at": "2026-06-24T00:00:00+00:00"})
    a = {r["account_id"]: r for r in repo.engaged_accounts()}["acc_e"]
    assert a["contacts"] == 3                  # opener + meeting + reactor; silent excluded
    assert a["delivered"] == 7                 # rates still over ALL recipients (3 + 4)


def test_engaging_contacts_helper_filters_and_dedupes():
    contacts = [
        {"external_id": "op", "email": "o@x.com", "opened": 1},                 # engaged
        {"external_id": "sil", "email": "s@x.com", "delivered": 5, "opened": 0},  # not
        {"external_id": "acct:1", "meeting_booked": True},                      # meeting, no email
        {"external_id": "li", "email": "r@x.com"},                              # engaged via event
    ]
    out = engaging_contacts(contacts, [{"contact_ext": "li", "points": 6}])
    assert len(out) == 3                                       # silent recipient dropped
    assert {c.get("email") for c in out} == {"o@x.com", "r@x.com", None}   # None = meeting row


def test_scores_before_sums_only_pre_cutoff_events(tmp_path):
    """The notifier's pre-cutoff baseline: only events strictly before the cutoff count,
    so an account already Warm before the cutoff isn't re-notified for that tier."""
    repo = _repo(tmp_path)
    repo.add_event(_event("e1", "linkedin_tofu", 6, account_id="acc_z",
                          at="2026-06-20T00:00:00+00:00"))    # before cutoff → counts
    repo.add_event(_event("e2", "meeting_booked", 10, account_id="acc_z",
                          at="2026-06-26T00:00:00+00:00"))    # after cutoff → excluded
    assert repo.scores_before("2026-06-25") == {"acc_z": 6}


def test_dedupe_contacts_merges_by_email_and_is_idempotent():
    contacts = [
        {"external_id": "sfdc-1", "source": "sfdc", "email": "a@x.com", "title": "CFO"},
        {"external_id": "rep-1", "source": "replyio", "email": "A@x.com",  # same person
         "sent": 1, "delivered": 1, "matched_lists": ["scored"]},
        {"external_id": "li-9", "source": "linkedin_ads", "email": "b@y.com"},   # different
        {"external_id": "no-mail", "source": "sfdc", "email": ""},               # blank → by id
    ]
    out = dedupe_contacts(contacts)
    assert len(out) == 3                                   # a@x.com merged; b + blank stay
    merged = next(c for c in out if c["email"] == "a@x.com")
    assert merged["delivered"] == 1 and merged["title"] == "CFO"   # stats summed, title kept
    assert merged["matched_lists"] == ["scored"]
    assert set(merged["sources"]) == {"sfdc", "replyio"}
    # idempotent — re-deduping yields the same people and preserves the source list
    again = dedupe_contacts(out)
    assert len(again) == 3
    assert set(next(c for c in again if c["email"] == "a@x.com")["sources"]) == {"sfdc", "replyio"}


def test_contact_person_key_prefers_email_then_external_id():
    assert contact_person_key({"email": "A@X.com"}) == "a@x.com"       # case-folded
    assert contact_person_key({"email": "", "external_id": "99"}) == "id:99"
    assert contact_person_key({"external_id": "77"}) == "id:77"


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
