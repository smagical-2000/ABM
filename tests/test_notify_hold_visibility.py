"""A HELD notify must never be silent (the 2026-07-28 incident).

The daily notify leg evaluated 4 due accounts, sent nothing, and told nobody:
the MAR2-32 audit interlock (most plausibly) went red during/just after an
identity heal, and the HELD ops alert sat inside its should_alert throttle
from a prior incident — swallowed. Two behaviours are pinned here:

  1. RETRY AFTER HEAL — a red audit on a write call runs the identity
     self-heal ONCE (the exact routine every sync/import runs), re-audits,
     and when the board comes back green proceeds with the send in the SAME
     request. Still red after the one retry -> hold, exactly as before.
  2. HOLD ALWAYS VISIBLE — the audit hold posts its ops alert with NO
     should_alert throttle, every hold stamps `notify_last_hold`, a clean
     write-mode pass stamps `notify_last_send`, and the daily digest renders
     `• notify: HELD …` whenever the newest hold is newer than the newest
     clean send.

No network: tests/conftest.py strips every Slack webhook (autouse), and the
alert/activation seams are monkeypatched to capture instead of post.
"""
from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from auto_search.db.engagement_repository import EngagementJsonRepository
from auto_search.engagement import audit, identity
from auto_search.engagement import notify as notify_mod

_app_module = importlib.import_module("auto_search.api.app")


@pytest.fixture
def client(tmp_path, monkeypatch):
    from auto_search.db.repository import JsonFileRepository
    from auto_search.db.scoring_repository import ScoringJsonRepository

    for var in ("BASIC_AUTH_USER", "BASIC_AUTH_PASS", "DATABASE_URL",
                "ENGAGEMENT_NOTIFY_SANE_MAX"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(_app_module, "get_repository",
                        lambda: JsonFileRepository(tmp_path / "s.json"))
    monkeypatch.setattr(_app_module, "get_scoring_repository",
                        lambda: ScoringJsonRepository(tmp_path / "sc.json"))
    monkeypatch.setattr(_app_module, "get_engagement_repository",
                        lambda: EngagementJsonRepository(tmp_path / "eng.json"))
    with TestClient(_app_module.create_app()) as c:
        yield c


def _seed_hot(repo, aid="abm_dueco", company="Due Co", *, healed=True):
    """Matrix-true Hot (26 = 10 + 10 + 6) ABM account, due to notify.
    healed=False leaves the ingest WITHOUT its heal marker — the I5-stale-heal
    red that the identity self-heal itself clears (the heal-fixable red)."""
    for ext, kind, pts in ((f"m:{aid}", "meeting_booked", 10),
                           (f"b:{aid}", "high_intent_lead", 10),
                           (f"r:{aid}", "reply", 6)):
        repo.add_event({"source": "replyio", "external_id": ext, "channel": "email",
                        "kind": kind, "points": pts, "contact_ext": f"c:{aid}",
                        "company": company, "account_id": aid,
                        "occurred_at": "2026-07-07T10:00:00+00:00", "raw": {}})
    # Activation is ABM-only (2026-07-22): membership reads off a contact's
    # matched_lists, so a due account needs one to pass the gate.
    repo.upsert_contact({"source": "replyio", "external_id": f"c:{aid}",
                         "company": company, "account_id": aid,
                         "matched_lists": ["abm"]})
    if healed:
        repo.set_setting("identity_heal_last", json.dumps(
            {"at": datetime.now(UTC).isoformat(), "merged": 0, "manual": 0}))


def _poison_points(repo, aid="abm_dueco"):
    """A stored event whose points defy the canonical matrix (I2-points) — a
    red NO identity heal can clear (only a data fix can)."""
    repo.add_event({"source": "replyio", "external_id": f"poison:{aid}",
                    "channel": "email", "kind": "click", "points": 9,
                    "contact_ext": f"c:{aid}", "company": "Due Co",
                    "account_id": aid,
                    "occurred_at": "2026-07-07T11:00:00+00:00", "raw": {}})


def _audit_red(client) -> bool:
    st = client.app.state
    return not audit.run_invariants(st.engagement_repo, st.scoring_repo,
                                    st.repo)["ok"]


# ── 1 · RETRY AFTER HEAL ──────────────────────────────────────────────────


def test_heal_fixable_red_heals_then_sends_in_the_same_call(client, monkeypatch):
    """THE incident shape, fixed: an audit red for a reason the identity heal
    clears (stale-heal marker after an unhealed ingest) must not eat the send
    — heal, re-audit, send, all inside one request."""
    repo = client.app.state.engagement_repo
    _seed_hot(repo, healed=False)          # ingest without heal marker -> I5 red
    assert _audit_red(client)              # the board IS red going in
    delivered = []
    monkeypatch.setattr(notify_mod, "activate_account",
                        lambda a, e, **kw: delivered.append(a["account_id"]) or True)
    out = client.post("/api/engagement/notify-changes?stage=live&dry_run=false").json()
    assert out.get("held") is None and out.get("stage") != "audit"
    assert out["due"] == 1 and out["posted"] == 1 and delivered == ["abm_dueco"]
    # the endpoint really healed (marker now present) — that is what cleared I5
    assert repo.get_setting("identity_heal_last")
    assert not _audit_red(client)


def test_still_red_after_one_heal_holds_and_heals_only_once(client, monkeypatch):
    """Points drift (I2) is not heal-fixable: the retry must run EXACTLY one
    heal, then hold exactly like before — never loop, never send."""
    repo = client.app.state.engagement_repo
    _seed_hot(repo, healed=False)
    _poison_points(repo)
    real = identity.heal_identity_splits
    heals = []

    def _spy(*a, **kw):
        if not kw.get("dry_run"):          # the audit's own dry-run probe doesn't count
            heals.append(1)
        return real(*a, **kw)

    monkeypatch.setattr(identity, "heal_identity_splits", _spy)
    sent = []
    monkeypatch.setattr(notify_mod, "activate_account",
                        lambda a, e, **kw: sent.append(1) or True)
    out = client.post("/api/engagement/notify-changes?stage=live&dry_run=false").json()
    assert out.get("held") is True and out.get("stage") == "audit"
    assert out["posted"] == 0 and sent == []
    assert heals == [1]                    # ONE retry, not a loop
    codes = {v["code"] for v in out["violations"]}
    assert "I2-points" in codes and "I5-stale-heal" not in codes
