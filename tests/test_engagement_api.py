"""Engagement API (Milestone F) — GET /api/engagement[/{id}] + POST sync.

Forces JSON repos (no Postgres) like test_api.py; monkeypatches the engagement
sync so the POST never hits the network.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

from auto_search.db.engagement_repository import EngagementJsonRepository
from auto_search.db.repository import JsonFileRepository
from auto_search.db.scoring_repository import ScoringJsonRepository

_app = importlib.import_module("auto_search.api.app")


async def _noop_sync(**_kwargs):
    return {}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("BASIC_AUTH_USER", raising=False)
    monkeypatch.delenv("BASIC_AUTH_PASS", raising=False)
    monkeypatch.setattr(_app, "get_repository",
                        lambda: JsonFileRepository(tmp_path / "d.json"))
    monkeypatch.setattr(_app, "get_scoring_repository",
                        lambda: ScoringJsonRepository(tmp_path / "s.json"))

    eng = EngagementJsonRepository(path=str(tmp_path / "e.json"))
    eng.upsert_contact({"external_id": "1", "account_id": "acc_x", "company": "Acme",
                        "delivered": 10, "opened": 4, "replied": 1,
                        "matched_lists": ["abm"]})
    eng.add_event({"external_id": "email:reply:1", "kind": "reply", "channel": "email",
                   "points": 6, "contact_ext": "1", "account_id": "acc_x",
                   "occurred_at": "2026-06-10T00:00:00+00:00"})
    eng.add_event({"external_id": "email:meeting_booked:1", "kind": "meeting_booked",
                   "channel": "email", "points": 10, "contact_ext": "1",
                   "account_id": "acc_x", "occurred_at": "2026-06-11T00:00:00+00:00"})
    monkeypatch.setattr(_app, "get_engagement_repository", lambda: eng)
    monkeypatch.setattr(_app.engagement_sync_mod, "run_sync", _noop_sync)

    from auto_search.api.app import create_app
    with TestClient(create_app()) as c:
        yield c


def test_get_engagement_ranks_with_tier_and_rates(client):
    body = client.get("/api/engagement").json()
    accts = body["accounts"]
    assert accts and accts[0]["account_id"] == "acc_x"
    a = accts[0]
    assert a["score"] == 16 and a["tier"] == "Warm"      # reply 6 + meeting 10
    assert a["open_rate"] == 40 and a["reply_rate"] == 10  # 4/10, 1/10
    assert a["lists"] == ["abm"]


def test_get_engagement_account_detail(client):
    r = client.get("/api/engagement/acc_x").json()
    assert r["account"]["account_id"] == "acc_x"
    assert {e["kind"] for e in r["events"]} == {"reply", "meeting_booked"}
    assert len(r["contacts"]) == 1


def test_get_unknown_account_404(client):
    assert client.get("/api/engagement/nope").status_code == 404


def test_engagement_fit_tier_reresolved_to_current_rubric(client):
    """The Slack card / Activity view must show the fit tier under TODAY's rubric, not
    the stale stored label (H1 guard), and surface the raw framework_key for AE routing
    (H2 guard)."""
    from datetime import UTC, datetime

    from auto_search.scoring.models import Account, Dimension, ScoreResult

    repo = client.app.state.scoring_repo
    repo.upsert_account(Account(account_id="acc_x", name="Acme", segment="health_system",
                                framework="health_system", source="discovery"), state="queued")
    repo.save_score("acc_x", ScoreResult(
        account_id="acc_x", framework="health_system", framework_version="hs-2026.2",
        dimensions=[Dimension(key="npr", label="NPR", score=8, max=10)],
        total=24, max_total=27, tier_band="high", tier_label="Tier 1",   # OLD resolution
        cost_usd=0.1, scored_at=datetime.now(UTC).isoformat()))

    a = {x["account_id"]: x for x in client.get("/api/engagement").json()["accounts"]}["acc_x"]
    assert a["fit_tier"] == "Tier 2"               # re-resolved (was stored Tier 1)
    assert a["framework_key"] == "health_system"   # raw key for AE routing


def test_recent_field_picks_meaningful_touch_excludes_noise(tmp_path, monkeypatch):
    """The Activity tab's `recent` field: the most significant MEANINGFUL touch in the
    last 14 days — meeting/lead/SAO over a click, click-only never surfaces, and old
    touches drop out of the window. Relative dates so it never ages out."""
    from datetime import UTC, datetime, timedelta

    monkeypatch.delenv("BASIC_AUTH_USER", raising=False)
    monkeypatch.delenv("BASIC_AUTH_PASS", raising=False)
    monkeypatch.setattr(_app, "get_repository", lambda: JsonFileRepository(tmp_path / "d2.json"))
    monkeypatch.setattr(_app, "get_scoring_repository",
                        lambda: ScoringJsonRepository(tmp_path / "s2.json"))
    eng = EngagementJsonRepository(path=str(tmp_path / "e2.json"))
    now = datetime.now(UTC)
    iso = lambda days: (now - timedelta(days=days)).isoformat()  # noqa: E731

    def seed(aid, ext, kind, points, days):
        eng.upsert_contact({"external_id": ext, "account_id": aid, "company": aid,
                            "matched_lists": ["abm"]})
        eng.add_event({"external_id": f"{kind}:{ext}", "kind": kind, "channel": "x",
                       "points": points, "contact_ext": ext, "account_id": aid,
                       "occurred_at": iso(days)})

    seed("acc_a", "a1", "click", 1, 1)              # A: a click …
    seed("acc_a", "a1b", "meeting_booked", 10, 3)   #    … and a meeting (meeting wins)
    seed("acc_b", "b1", "click", 1, 1)              # B: click-only → not surfaced
    seed("acc_c", "c1", "meeting_booked", 10, 40)   # C: meaningful but 40d old → out of window
    monkeypatch.setattr(_app, "get_engagement_repository", lambda: eng)
    monkeypatch.setattr(_app.engagement_sync_mod, "run_sync", _noop_sync)

    from auto_search.api.app import create_app
    with TestClient(create_app()) as c:
        accts = {a["account_id"]: a for a in c.get("/api/engagement").json()["accounts"]}
    assert accts["acc_a"]["recent"]["kind"] == "meeting_booked"   # meaningful over the click
    assert accts["acc_b"]["recent"] is None                       # click-only never surfaces
    assert accts["acc_c"]["recent"] is None                       # outside the 14-day window


def test_activate_test_mode_skips_enrichment_credit_safety(client, monkeypatch):
    """Credit-safety gate: a {"test": true} activation must NOT enrich (no Apollo/
    FullEnrich spend); a real Hot activation enriches once. Slack is stubbed out."""
    from auto_search.db.scoring_repository import ScoringJsonRepository
    from auto_search.engagement import enrichment, notify

    # Push acc_x to Hot (fixture has 16 = Warm; add a BOFU event → 26 = Hot)
    repo = client.app.state.engagement_repo
    repo.add_event({"external_id": "sfdc:bofu:1", "kind": "high_intent_lead",
                    "channel": "sfdc", "points": 10, "contact_ext": "1",
                    "account_id": "acc_x", "occurred_at": "2026-06-12T00:00:00+00:00"})

    calls = []

    async def fake_enrich(domain, *, company=None):
        calls.append(domain)
        return [{"name": "X", "title": "VP RevCycle", "email": "x@acme.com", "phone": "+1 5"}]

    monkeypatch.setattr(enrichment, "enrich_account", fake_enrich)
    monkeypatch.setattr(notify, "activate_account", lambda *a, **k: True)   # no real Slack
    monkeypatch.setattr(ScoringJsonRepository, "get",
                        lambda self, aid: {"name": "Acme", "domain": "acme.com"}
                        if aid == "acc_x" else None)

    r1 = client.post("/api/engagement/acc_x/activate", json={"test": True})
    assert r1.status_code == 200 and r1.json()["posted"] is True
    assert calls == []                       # test post spent zero enrichment credits

    r2 = client.post("/api/engagement/acc_x/activate", json={})
    assert r2.status_code == 200 and calls == ["acme.com"]   # Hot activation enriched once
    assert r2.json()["contacts"][0]["email"] == "x@acme.com"


def test_warm_activation_routes_to_sdr_no_enrichment(client, monkeypatch):
    """Warm accounts route to the SDR (not AE) and skip enrichment (no credits spent)."""
    from auto_search.engagement import enrichment, notify

    enrich_calls = []

    async def fake_enrich(domain, *, company=None):
        enrich_calls.append(domain)
        return [{"name": "X", "title": "VP", "email": "x@x.com", "phone": None}]

    activate_kwargs = {}

    def capture_activate(*_args, **kw):
        activate_kwargs.update(kw)
        return True

    monkeypatch.setattr(enrichment, "enrich_account", fake_enrich)
    monkeypatch.setattr(notify, "activate_account", capture_activate)
    monkeypatch.setattr(notify, "resolve_sdr",
                        lambda acct, **_kw: "@Ben Davies")
    monkeypatch.setattr(notify, "resolve_ae", lambda acct, **_kw: None)

    # The fixture seeds acc_x with reply(6) + meeting_booked(10) = 16 → Warm
    r = client.post("/api/engagement/acc_x/activate", json={})
    assert r.status_code == 200
    assert r.json()["routed_to"] == "@Ben Davies"
    assert enrich_calls == []          # Warm = no enrichment spend
    assert activate_kwargs["ae"] == "@Ben Davies"
    assert activate_kwargs["dm_limit"] == 0


def test_export_csv_has_header_and_rows(client):
    r = client.get("/api/engagement/export.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=magical-engagement.csv" in r.headers.get("content-disposition", "")
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("Account,Domain,Classification")
    assert any("acc_x" in ln or "," in ln for ln in lines[1:])   # at least one data row


def test_sync_endpoint_starts_background(client):
    res = client.post("/api/engagement/sync").json()
    assert res["started"] is True
