"""Notify-changes durability: the ledger must be written the moment a card
lands, not once at the end of the send loop.

Two production failure modes are pinned here (COO QA sweep, 2026-07-27):

1. **Partial state.** Cards post to Slack inside the loop but `set_setting`
   only ran after it. Any mid-loop raise (a dropped psycopg connection in
   `events_for_account`, an OOM/deploy restart) left every already-delivered
   card unrecorded — the next trigger, 15 minutes later, re-posted all of them
   to the live AE/SDR channels.

2. **Read-modify-write race.** The endpoint is a sync `def` (threadpool =
   real parallelism) and three triggers can overlap (daily cron leg, the TOFU
   runner's event-driven push, a human in the console). Each loaded the whole
   ledger, held it in memory for the length of a send loop, and wrote it back
   wholesale — last writer silently discarded the other run's entries, so those
   accounts became "due" again and re-fired.
"""

from __future__ import annotations

import importlib
import json

import pytest
from fastapi.testclient import TestClient

from auto_search.engagement import notify as notify_mod

_app_module = importlib.import_module("auto_search.api.app")


@pytest.fixture
def client(tmp_path, monkeypatch):
    from auto_search.db.engagement_repository import EngagementJsonRepository
    from auto_search.db.repository import JsonFileRepository
    from auto_search.db.scoring_repository import ScoringJsonRepository

    for var in ("BASIC_AUTH_USER", "BASIC_AUTH_PASS", "DATABASE_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(_app_module, "get_repository",
                        lambda: JsonFileRepository(tmp_path / "s.json"))
    monkeypatch.setattr(_app_module, "get_scoring_repository",
                        lambda: ScoringJsonRepository(tmp_path / "sc.json"))
    monkeypatch.setattr(_app_module, "get_engagement_repository",
                        lambda: EngagementJsonRepository(tmp_path / "eng.json"))
    with TestClient(_app_module.create_app()) as c:
        yield c


def _seed_due_account(app, key: str) -> None:
    """One Hot account with MATRIX-TRUE points (10+10+6=26), not in the ledger
    -> due to notify. Points must match the canonical matrix or the MAR2-32
    audit interlock holds the whole send."""
    repo = app.state.engagement_repo
    repo.upsert_contact({"source": "replyio", "external_id": f"c_{key}",
                         "email": f"a@{key}.com", "email_domain": f"{key}.com",
                         "company": key.title(), "company_key": key,
                         "account_id": f"abm_{key}", "match_tier": "domain",
                         "matched_lists": ["abm"], "delivered": 1})
    for ext, kind, pts in (("meet", "meeting_booked", 10),
                           ("bofu", "high_intent_lead", 10),
                           ("reply", "reply", 6)):
        repo.add_event({"source": "replyio", "external_id": f"e:{ext}:{key}",
                        "channel": "email", "kind": kind, "points": pts,
                        "contact_ext": f"c_{key}", "company": key.title(),
                        "account_id": f"abm_{key}",
                        "occurred_at": "2026-07-07T10:00:00+00:00"})
    from datetime import UTC, datetime
    repo.set_setting("identity_heal_last", json.dumps(
        {"at": datetime.now(UTC).isoformat(), "merged": 0, "manual": 0}))


def _ledger(app) -> dict:
    return json.loads(app.state.engagement_repo.get_setting("notified_tiers") or "{}")


def test_delivered_card_is_recorded_before_a_mid_loop_error(client, monkeypatch):
    """The card that DID post must survive an exception on the next account —
    otherwise the next trigger re-posts it to the real AE/SDR channel."""
    app = client.app
    _seed_due_account(app, "dueco")
    _seed_due_account(app, "secondco")

    delivered: list[str] = []

    def _activate(a, _events, **_kw):
        if delivered:                       # second account of the run explodes
            raise RuntimeError("dropped connection mid-loop")
        delivered.append(notify_mod.ledger_key(a))
        return True

    monkeypatch.setattr(notify_mod, "activate_account", _activate)

    with pytest.raises(RuntimeError):
        client.post("/api/engagement/notify-changes?stage=live&dry_run=false")

    assert len(delivered) == 1              # exactly one card went out...
    led = _ledger(app)
    assert set(led) == set(delivered)       # ...and it IS recorded, despite the raise


def test_concurrent_writers_entries_are_not_clobbered(client, monkeypatch):
    """A second notifier that finished while we were sending must keep its
    ledger entry: we re-read and merge-strongest at write time instead of
    blindly writing the copy we loaded before the loop."""
    app = client.app
    repo = app.state.engagement_repo
    _seed_due_account(app, "dueco")

    def _activate(_a, _events, **_kw):
        # A concurrent notify-changes caller records ITS account while our send
        # loop is mid-flight (it read the same empty ledger we did).
        led = json.loads(repo.get_setting("notified_tiers") or "{}")
        led["rivalco"] = {"tier": "Hot", "touch": "2026-07-20T00:00:00+00:00",
                          "account_id": "abm_rivalco", "name": "Rival Co"}
        repo.set_setting("notified_tiers", json.dumps(led))
        return True

    monkeypatch.setattr(notify_mod, "activate_account", _activate)

    out = client.post("/api/engagement/notify-changes?stage=live&dry_run=false").json()
    assert out["posted"] == 1
    led = _ledger(app)
    assert "abm_dueco" in led               # ours
    assert "rivalco" in led                 # theirs — not clobbered
