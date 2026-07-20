"""SHIP batch 2026-07-20 — regression guards for the reliability fixes.

Each test pins one incident from the 7/16–7/20 forensic sessions:
click-storm inflation (AGT-1453), compound-BOFU invisibility (Trevor/FCS),
TOFU-echo double-count risk (Crystal) + dupe leads (Pamela), the non-Intro
meeting false Hot (Commonwealth), the QA-alert scare, and the reactivation
card copy — plus the 8-angle review follow-ups: the drawer's uncapped score,
the /due evaluator parity, the api-anchored I6 fleet check, and the
multi-email echo-dupe collapse.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from auto_search.engagement import notify, scoring
from auto_search.engagement import sfdc as sfdc_mod
from auto_search.engagement.sfdc_client import SalesforceClient
from auto_search.normalize import normalize_company_name
from auto_search.ops import alerts, heartbeat

_app = importlib.import_module("auto_search.api.app")

# ── click cap (AGT-1453) ─────────────────────────────────────────────────


def test_capped_score_math():
    assert scoring.capped_score(37, 37) == 3          # pure scanner storm → 3
    assert scoring.capped_score(12, 2) == 12          # under cap: untouched
    assert scoring.capped_score(94, 32) == 65         # Ivy: 94 raw → 65 capped
    assert scoring.capped_score(0, 0) == 0


def test_json_repo_board_and_baseline_apply_cap(tmp_path):
    from auto_search.db.engagement_repository import EngagementJsonRepository
    repo = EngagementJsonRepository(tmp_path / "e.json")
    for i in range(7):                                 # 7 click pts (cap → 3)
        repo.add_event({"source": "smartlead", "external_id": f"c{i}",
                        "channel": "email", "kind": "outbound_click", "points": 1,
                        "contact_ext": f"p{i}", "company": "Acme",
                        "account_id": "abm_acme",
                        "occurred_at": f"2026-07-0{i + 1}T00:00:00+00:00", "raw": {}})
    repo.add_event({"source": "replyio", "external_id": "r1", "channel": "email",
                    "kind": "reply", "points": 6, "contact_ext": "p9",
                    "company": "Acme", "account_id": "abm_acme",
                    "occurred_at": "2026-07-08T00:00:00+00:00", "raw": {}})
    row = next(r for r in repo.engaged_accounts() if r["account_id"] == "abm_acme")
    assert row["score"] == 9                           # 3 (capped clicks) + 6
    assert repo.scores_before("2026-07-09")["abm_acme"] == 9


def test_audit_recompute_matches_capped_board(tmp_path):
    from auto_search.db.engagement_repository import EngagementJsonRepository
    from auto_search.engagement import audit
    repo = EngagementJsonRepository(tmp_path / "e.json")
    for i in range(5):
        repo.add_event({"source": "smartlead", "external_id": f"c{i}",
                        "channel": "email", "kind": "outbound_click", "points": 1,
                        "contact_ext": f"p{i}", "company": "Acme",
                        "account_id": "abm_acme",
                        "occurred_at": f"2026-07-0{i + 1}T00:00:00+00:00", "raw": {}})
    board = [{"account_id": "abm_acme", "score": 3,     # what the view now serves
              "last_touch": "2026-07-05T00:00:00+00:00", "name": "Acme"}]
    res = audit.run_invariants(repo, None, None, rows=board)
    assert not [v for v in res["violations"] if v["code"].startswith("I3")]


# ── SFDC filters ─────────────────────────────────────────────────────────


def _captured_queries():
    c = SalesforceClient.__new__(SalesforceClient)   # no creds needed
    seen: list[str] = []
    c.query = lambda q: seen.append(q) or iter(())   # type: ignore[method-assign]
    return c, seen


def test_high_intent_catches_compound_bofu():
    c, seen = _captured_queries()
    list(c.iter_high_intent_leads(since="2026-01-01"))
    assert "LIKE '%| BOFU'" in seen[0]                # 'CS Headspace | BOFU' class


def test_low_intent_includes_tofu_echo_label():
    c, seen = _captured_queries()
    list(c.iter_low_intent_leads(since="2026-01-01"))
    assert "TOFU Engagement Campaign" in seen[0]


def _lead(lid, email, src="TOFU Engagement Campaign", created="2026-07-19T00:00:00Z"):
    return {"Id": lid, "Email": email, "Company": "Acme Health",
            "LeadSource": src, "CreatedDate": created}


def test_tofu_echo_suppressed_and_dupes_collapse():
    leads = [_lead("L2", "pam@kp.org", created="2026-07-20T00:00:00Z"),   # dupe (newer)
             _lead("L1", "pam@kp.org", created="2026-07-19T00:00:00Z"),   # canonical
             _lead("L3", "crystal@aol.com", created="2026-07-20T13:00:00Z"),  # captured
             _lead("L4", "fresh@acme.com", created="2026-07-20T14:00:00Z")]   # genuine
    cs, es = sfdc_mod.parse_leads(leads, kind="low_intent_lead", channel="content",
                                  campaign_field="LeadSource", now="2026-07-20T15:00:00+00:00")
    cs, es = sfdc_mod.filter_tofu_echoes(cs, es, captured_emails={"crystal@aol.com"})
    kept = {e["contact_ext"] for e in es}
    assert kept == {"L1", "L4"}                       # dupe→oldest; captured dropped
    assert {c["external_id"] for c in cs} == {"L1", "L4"}


def test_meetings_intro_only():
    meetings = [
        {"Id": "M1", "Subject": "Acme / Magical Introductory Call", "Type": "Meeting",
         "AccountId": "A1", "Account": {"Name": "Acme Health"},
         "CreatedDate": "2026-07-10T00:00:00Z", "StartDateTime": "2026-07-10T00:00:00Z"},
        {"Id": "M2", "Subject": "Acme <> Magical Technical Demo", "Type": "Meeting",
         "AccountId": "A1", "Account": {"Name": "Acme Health"},
         "CreatedDate": "2026-07-17T00:00:00Z", "StartDateTime": "2026-07-17T00:00:00Z"},
    ]
    _, events = sfdc_mod.parse(meetings, [], now="2026-07-20T00:00:00+00:00")
    mtg = [e for e in events if e["kind"] == "meeting_booked"]
    assert len(mtg) == 1
    assert "Introductory" in str(mtg[0]["raw"])       # the demo did NOT count


# ── notify + alerts UX ───────────────────────────────────────────────────


def test_reactivation_card_copy():
    acct = {"name": "Kaiser Permanente", "tier": "Hot", "score": 29}
    card = notify.build_card(acct, [], ae="<@U123>", reason="hot_activity")
    text = str(card)
    assert "Hot again" in text and "move to status" not in text
    card2 = notify.build_card(acct, [], ae="<@U123>")
    assert "move to status Hot" in str(card2)


def test_qa_alert_prefix_and_runbook():
    card = alerts.build_alert_card(kind="t", title="Notify HELD", qa=True,
                                   runbook="clear the ceiling")
    text = str(card)
    assert "[QA · ALERT]" in text and "What to do" in text
    assert "[QA" not in str(alerts.build_alert_card(kind="t", title="x"))


# ── second-wave fixes (2026-07-20 8-angle review) ────────────────────────


@pytest.fixture
def client(tmp_path, monkeypatch):
    """API client on JSON repos (mirrors tests/test_heyreach_connect.py)."""
    from auto_search.db.engagement_repository import EngagementJsonRepository
    from auto_search.db.repository import JsonFileRepository
    from auto_search.db.scoring_repository import ScoringJsonRepository
    for var in ("BASIC_AUTH_USER", "BASIC_AUTH_PASS", "DATABASE_URL"):
        monkeypatch.delenv(var, raising=False)
    repo = JsonFileRepository(tmp_path / "s.json")
    eng = EngagementJsonRepository(tmp_path / "eng.json")
    monkeypatch.setattr(_app, "get_repository", lambda: repo)
    monkeypatch.setattr(_app, "get_scoring_repository",
                        lambda: ScoringJsonRepository(tmp_path / "sc.json"))
    monkeypatch.setattr(_app, "get_engagement_repository", lambda: eng)
    with TestClient(_app.create_app()) as c:
        c._eng = eng
        yield c


def _seed_clicky_account(eng, account_id="abm_acme"):
    """5 click pts (cap → 3) + 1 reply (6): raw 11, capped 9."""
    for i in range(5):
        eng.add_event({"source": "replyio", "external_id": f"c{i}", "channel": "email",
                       "kind": "click", "points": 1, "contact_ext": f"p{i}",
                       "company": "Acme", "account_id": account_id,
                       "occurred_at": f"2026-07-1{i}T00:00:00+00:00", "raw": {}})
    eng.add_event({"source": "replyio", "external_id": "r1", "channel": "email",
                   "kind": "reply", "points": 6, "contact_ext": "p9",
                   "company": "Acme", "account_id": account_id,
                   "occurred_at": "2026-07-16T00:00:00+00:00", "raw": {}})


def test_drawer_account_score_applies_click_cap(client):
    """The drawer (_engaged_one, also the manual /activate packet) summed
    UNCAPPED points — a scanner storm inflated the sales-facing score even
    after the board/baseline/audit were capped."""
    _seed_clicky_account(client._eng)
    r = client.get("/api/engagement/abm_acme")
    assert r.status_code == 200
    acct = r.json()["account"]
    assert acct["score"] == 9                      # capped: 3 click pts + 6, not 11
    assert acct["tier"] == scoring.tier_for(9)     # tier derives from the capped score
    assert acct["clicks"] == 5                     # raw counts still shown in full


def test_due_endpoint_matches_notify_changes_dry_run(client):
    """GET /api/engagement/due IS the evaluator (hand-built send lists are
    banned): same shape and same due count as notify-changes?dry_run=true."""
    _seed_clicky_account(client._eng)              # capped 9 → Some → SDR-due
    due = client.get("/api/engagement/due").json()
    dry = client.post("/api/engagement/notify-changes", params={"dry_run": "true"}).json()
    assert "due" in due and "detail" in due
    assert "due" in dry and "detail" in dry
    assert due["due"] == dry["due"] == 1
    assert due["detail"][0]["account_id"] == "abm_acme"


def test_stale_writer_after_api_beat_is_flagged(tmp_path, monkeypatch):
    """I6, api-anchored: only a service that beat AFTER the API's own boot beat
    on a DIFFERENT build is stale — pre-deploy beats (however recent) and
    same-build beats never flag, so a routine deploy can't false-red the fleet."""
    from auto_search.db.engagement_repository import EngagementJsonRepository
    repo = EngagementJsonRepository(tmp_path / "e.json")
    monkeypatch.setenv("BUILD_STAMP", "ship-old")
    heartbeat.beat("pre-deploy-cron", repo=repo)   # old build, but BEFORE the api beat
    # No api anchor yet → nothing can be judged stale.
    assert heartbeat.stale_writers(heartbeat.read_stamps(repo), "ship-new") == {}
    monkeypatch.setenv("BUILD_STAMP", "ship-new")
    heartbeat.beat("api", repo=repo)               # the anchor (app boot)
    monkeypatch.setenv("BUILD_STAMP", "ship-old")
    heartbeat.beat("stale-cron", repo=repo)        # old build AFTER the api → stale
    monkeypatch.setenv("BUILD_STAMP", "ship-new")
    heartbeat.beat("fresh-cron", repo=repo)        # new build after the api → clean
    stamps = heartbeat.read_stamps(repo)
    assert heartbeat.stale_writers(stamps, "ship-new") == {"stale-cron": "ship-old"}


def test_tofu_echo_person_key_collapses_multi_email_dupes():
    """The Pamela case: the Airtable automation re-created her lead when
    enrichment landed a SECOND email — one human, two SFDC leads, two
    addresses. Email-keyed dedupe can't see it; the person key
    (name+company, built by collect_sfdc_rows from the raw leads) can."""
    leads = [_lead("L1", "pamela.mixon@kp.org", created="2026-07-19T00:00:00Z"),
             _lead("L2", "pmixon@outlook.com", created="2026-07-20T00:00:00Z")]
    cs, es = sfdc_mod.parse_leads(leads, kind="low_intent_lead", channel="content",
                                  campaign_field="LeadSource", now="2026-07-20T15:00:00+00:00")
    pk = normalize_company_name("Pamela Mixon Acme Health")  # collect_sfdc_rows' key shape
    cs, es = sfdc_mod.filter_tofu_echoes(cs, es, captured_emails=set(),
                                         person_key_by_lid={"L1": pk, "L2": pk})
    assert {c["external_id"] for c in cs} == {"L1"}          # oldest lead is canonical
    assert {e["contact_ext"] for e in es} == {"L1"}
