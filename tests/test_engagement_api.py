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


def _noop_sync_blocking(**_kwargs):
    return {}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("BASIC_AUTH_USER", raising=False)
    monkeypatch.delenv("BASIC_AUTH_PASS", raising=False)
    # The consolidated /sync runs EVERY source in the background; stub them all so a
    # test that triggers it never makes a real SFDC/Apify/Reply.io call.
    monkeypatch.delenv("PODCAST_CSV_URL", raising=False)
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
    monkeypatch.setattr(_app.engagement_sync_mod, "run_sfdc_sync", _noop_sync_blocking)
    monkeypatch.setattr(_app.engagement_sync_mod, "run_podcast_url_sync", _noop_sync_blocking)
    import auto_search.engagement.linkedin_ads_runner as _li
    monkeypatch.setattr(_li, "run", _noop_sync)
    # _linkedin_tofu builds these BEFORE the (stubbed) runner; stub them too so the
    # sync tests don't depend on AIRTABLE_*/REPLYIO env (absent in CI — this was the
    # "works on my machine" red CI: the linkedin leg crashed constructing AirtableClient).
    import auto_search.engagement.airtable_client as _atc
    import auto_search.engagement.replyio_client as _rc
    monkeypatch.setattr(_atc, "AirtableClient", lambda *a, **k: object())
    monkeypatch.setattr(_rc, "ReplyioClient", lambda *a, **k: object())

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


def test_warm_routes_to_sdr_with_full_packet(client, monkeypatch):
    """Warm → SDR with the SAME enriched packet as an AE (same process + information):
    Apollo enrich runs and the card carries the 2 decision-makers."""
    from auto_search.db.scoring_repository import ScoringJsonRepository
    from auto_search.engagement import enrichment, notify

    monkeypatch.setattr(ScoringJsonRepository, "get",
                        lambda self, aid: {"name": "Acme", "domain": "acme.com"}
                        if aid == "acc_x" else None)
    enrich_calls = []

    async def fake_enrich(domain, *, company=None):
        enrich_calls.append(domain)
        return [{"name": "X", "title": "VP", "email": "x@x.com", "phone": None}]

    kw = {}
    monkeypatch.setattr(enrichment, "enrich_account", fake_enrich)
    monkeypatch.setattr(notify, "activate_account", lambda *a, **k: (kw.update(k) or True))
    monkeypatch.setattr(notify, "resolve_sdr", lambda acct, **_kw: "@Ben Davies")
    monkeypatch.setattr(notify, "resolve_ae", lambda acct, **_kw: None)

    r = client.post("/api/engagement/acc_x/activate", json={})   # acc_x = 16 → Warm
    assert r.status_code == 200 and r.json()["routed_to"] == "@Ben Davies"
    assert enrich_calls == ["acme.com"]      # Warm now enriches, same as the AE card
    assert kw["ae"] == "@Ben Davies" and kw["dm_limit"] == 2


def test_some_tier_routes_to_sdr(client, monkeypatch):
    """An account in the Some tier (6-11) also routes to the SDR (not just Warm)."""
    from auto_search.engagement import notify

    eng = client.app.state.engagement_repo
    eng.upsert_contact({"external_id": "some1", "account_id": "acc_some",
                        "company": "SomeCo", "matched_lists": ["abm"]})
    eng.add_event({"external_id": "email:reply:some1", "kind": "reply", "channel": "email",
                   "points": 6, "contact_ext": "some1", "account_id": "acc_some",
                   "occurred_at": "2026-06-10T00:00:00+00:00"})   # score 6 → Some
    kw = {}
    monkeypatch.setattr(notify, "activate_account", lambda *a, **k: (kw.update(k) or True))
    monkeypatch.setattr(notify, "resolve_sdr", lambda acct, **_kw: "@Gabriel")
    monkeypatch.setattr(notify, "resolve_ae", lambda acct, **_kw: None)

    r = client.post("/api/engagement/acc_some/activate", json={})
    assert r.status_code == 200 and r.json()["routed_to"] == "@Gabriel"
    assert kw["ae"] == "@Gabriel"


def test_activation_testing_mode_private_webhook_plain_name(client, monkeypatch):
    """Default (no ENGAGEMENT_LIVE_ROUTING): the card stays on the private webhook
    (None → SLACK_ENGAGEMENT_WEBHOOK) with a plain @Name — testing never pings a real
    person or posts to the AE/SDR channel, even with all the live config present."""
    from auto_search.engagement import enrichment, notify

    monkeypatch.delenv("ENGAGEMENT_LIVE_ROUTING", raising=False)
    monkeypatch.setenv("DEFAULT_SDR", "Gabriel")
    monkeypatch.setenv("SDR_SLACK_IDS", "Gabriel=U096")
    monkeypatch.setenv("SLACK_SDR_WEBHOOK", "https://hooks.test/sdr")

    async def fake_enrich(domain, *, company=None):
        return []

    kw = {}
    monkeypatch.setattr(enrichment, "enrich_account", fake_enrich)
    monkeypatch.setattr(notify, "activate_account", lambda *a, **k: (kw.update(k) or True))

    r = client.post("/api/engagement/acc_x/activate", json={})   # acc_x Warm → SDR
    assert r.status_code == 200
    assert kw["webhook"] is None             # falls back to the private testing line
    assert kw["ae"] == "@Gabriel"            # plain name, NOT <@U096> — no real ping


def test_activation_live_mode_routes_to_sdr_channel_and_pings(client, monkeypatch):
    """ENGAGEMENT_LIVE_ROUTING=1: a Warm/Some card routes to the SDR channel webhook
    and @-pings the real SDR member id."""
    from auto_search.engagement import enrichment, notify

    monkeypatch.setenv("ENGAGEMENT_LIVE_ROUTING", "1")
    monkeypatch.setenv("DEFAULT_SDR", "Gabriel")
    monkeypatch.setenv("SDR_SLACK_IDS", "Gabriel=U096")
    monkeypatch.setenv("SLACK_SDR_WEBHOOK", "https://hooks.test/sdr")

    async def fake_enrich(domain, *, company=None):
        return []

    kw = {}
    monkeypatch.setattr(enrichment, "enrich_account", fake_enrich)
    monkeypatch.setattr(notify, "activate_account", lambda *a, **k: (kw.update(k) or True))

    r = client.post("/api/engagement/acc_x/activate", json={})
    assert r.status_code == 200
    assert kw["webhook"] == "https://hooks.test/sdr"   # routed to the SDR channel
    assert kw["ae"] == "<@U096>"                        # real ping


def test_activation_test_post_stays_private_even_when_live(client, monkeypatch):
    """A {"test": true} post must NOT hit the real channel or ping anyone even when
    live routing is ON — it's how we safely smoke-test from the private line."""
    from auto_search.engagement import notify

    monkeypatch.setenv("ENGAGEMENT_LIVE_ROUTING", "1")
    monkeypatch.setenv("DEFAULT_SDR", "Gabriel")
    monkeypatch.setenv("SDR_SLACK_IDS", "Gabriel=U096")
    monkeypatch.setenv("SLACK_SDR_WEBHOOK", "https://hooks.test/sdr")

    kw = {}
    monkeypatch.setattr(notify, "activate_account", lambda *a, **k: (kw.update(k) or True))

    r = client.post("/api/engagement/acc_x/activate", json={"test": True})
    assert r.status_code == 200
    assert kw["webhook"] is None and kw["ae"] == "@Gabriel"   # private line, plain name


def test_live_routing_toggle_get_and_set(client, monkeypatch):
    """The console toggle reads/writes the live-routing state and persists it."""
    monkeypatch.delenv("ENGAGEMENT_LIVE_ROUTING", raising=False)
    s = client.get("/api/engagement/settings/live-routing").json()
    assert s == {"enabled": False, "source": "env"}      # default off, from env
    s2 = client.post("/api/engagement/settings/live-routing", json={"enabled": True}).json()
    assert s2 == {"enabled": True, "source": "override"}  # toggled on, now an override
    assert client.get("/api/engagement/settings/live-routing").json()["enabled"] is True


def test_runtime_override_on_beats_env_off(client, monkeypatch):
    """Flipping the UI toggle ON routes live even when the env default is OFF — no
    redeploy needed."""
    from auto_search.engagement import enrichment, notify

    monkeypatch.delenv("ENGAGEMENT_LIVE_ROUTING", raising=False)   # env default OFF
    monkeypatch.setenv("DEFAULT_SDR", "Gabriel")
    monkeypatch.setenv("SDR_SLACK_IDS", "Gabriel=U096")
    monkeypatch.setenv("SLACK_SDR_WEBHOOK", "https://hooks.test/sdr")

    async def fake_enrich(domain, *, company=None):
        return []

    kw = {}
    monkeypatch.setattr(enrichment, "enrich_account", fake_enrich)
    monkeypatch.setattr(notify, "activate_account", lambda *a, **k: (kw.update(k) or True))

    client.post("/api/engagement/settings/live-routing", json={"enabled": True})   # UI flips it on
    r = client.post("/api/engagement/acc_x/activate", json={})            # acc_x Warm → SDR
    assert r.status_code == 200
    assert kw["webhook"] == "https://hooks.test/sdr"   # routed live despite env off
    assert kw["ae"] == "<@U096>"                        # real ping


def test_runtime_override_off_beats_env_on(client, monkeypatch):
    """Flipping the UI toggle OFF keeps testing private even when env says live — the
    console is the source of truth (an emergency 'stop pinging' switch)."""
    from auto_search.engagement import enrichment, notify

    monkeypatch.setenv("ENGAGEMENT_LIVE_ROUTING", "1")   # env default ON
    monkeypatch.setenv("DEFAULT_SDR", "Gabriel")
    monkeypatch.setenv("SDR_SLACK_IDS", "Gabriel=U096")
    monkeypatch.setenv("SLACK_SDR_WEBHOOK", "https://hooks.test/sdr")

    async def fake_enrich(domain, *, company=None):
        return []

    kw = {}
    monkeypatch.setattr(enrichment, "enrich_account", fake_enrich)
    monkeypatch.setattr(notify, "activate_account", lambda *a, **k: (kw.update(k) or True))

    client.post("/api/engagement/settings/live-routing", json={"enabled": False})   # UI flips it off
    r = client.post("/api/engagement/acc_x/activate", json={})
    assert r.status_code == 200
    assert kw["webhook"] is None and kw["ae"] == "@Gabriel"   # back to private + plain name


def test_activate_dedups_across_users(client, monkeypatch):
    """Two reps activating the same account → it posts to Slack ONCE; the second gets
    already_activated with no spend. `force` deliberately re-activates."""
    from auto_search.engagement import notify

    posts = []
    monkeypatch.setattr(notify, "activate_account",
                        lambda *a, **k: (posts.append(1) or True))

    r1 = client.post("/api/engagement/acc_x/activate", json={}).json()
    assert r1["posted"] is True

    r2 = client.post("/api/engagement/acc_x/activate", json={}).json()   # another rep
    assert r2 == {"posted": False, "already_activated": True, "account_id": "acc_x"}
    assert len(posts) == 1                       # posted exactly once

    r3 = client.post("/api/engagement/acc_x/activate", json={"force": True}).json()
    assert r3["posted"] is True and r3["reactivated"] is True
    assert len(posts) == 2                       # force re-posts


def test_activate_releases_claim_on_slack_failure(client, monkeypatch):
    """If the Slack post fails after claiming, the claim is released so a retry works
    (no account left stuck 'activated' but never posted)."""
    from auto_search.engagement import notify

    monkeypatch.setattr(notify, "activate_account", lambda *a, **k: False)   # Slack down
    r = client.post("/api/engagement/acc_x/activate", json={})
    assert r.status_code == 502
    assert client.app.state.engagement_repo.is_activated("acc_x") is False   # released


def test_test_activation_is_never_deduped(client, monkeypatch):
    """A {"test": true} wiring post always fires and never claims — so it can be
    repeated and never blocks a real activation."""
    from auto_search.engagement import notify
    posts = []
    monkeypatch.setattr(notify, "activate_account",
                        lambda *a, **k: (posts.append(1) or True))
    client.post("/api/engagement/acc_x/activate", json={"test": True})
    client.post("/api/engagement/acc_x/activate", json={"test": True})
    assert len(posts) == 2                       # both test posts fire
    assert client.app.state.engagement_repo.is_activated("acc_x") is False   # never claimed


def test_board_shows_activated_and_reset_clears_it(client):
    """The board badges activated accounts; the reset endpoint clears the ledger so
    SDRs/AEs can re-activate during testing."""
    client.app.state.engagement_repo.claim_activation("acc_x")
    a = {x["account_id"]: x for x in client.get("/api/engagement").json()["accounts"]}["acc_x"]
    assert a["activated"] is True
    r = client.post("/api/engagement/activations/reset", json={}).json()
    assert r["reset"] == 1
    a2 = {x["account_id"]: x for x in client.get("/api/engagement").json()["accounts"]}["acc_x"]
    assert a2["activated"] is False


def test_reset_single_activation_by_account_id(client):
    repo = client.app.state.engagement_repo
    repo.claim_activation("acc_x")
    r = client.post("/api/engagement/activations/reset", json={"account_id": "acc_x"}).json()
    assert r == {"reset": 1, "account_id": "acc_x"}
    assert repo.is_activated("acc_x") is False


def test_activation_deep_links_to_account(client, monkeypatch):
    """The Slack 'Open in console' link deep-links to the account's drawer
    (?view=engagement&account=…), not the generic console home."""
    from auto_search.engagement import notify

    monkeypatch.setenv("ENGAGEMENT_APP_URL", "https://console.test/eng")
    kw = {}
    monkeypatch.setattr(notify, "activate_account", lambda *a, **k: (kw.update(k) or True))
    client.post("/api/engagement/acc_x/activate", json={})
    assert kw["app_url"] == "https://console.test/eng?view=engagement&account=acc_x"


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


def test_sync_runs_all_sources_best_effort(client, monkeypatch):
    """The one Sync button pulls EVERY source in one pass — Reply.io, SFDC, podcast,
    LinkedIn TOFU — and a failing leg never skips the rest (best-effort)."""
    import time

    called: list[str] = []

    async def rec_replyio(**_k):
        called.append("replyio")
        raise RuntimeError("boom")          # first leg fails — rest must still run

    def rec_sfdc(**_k):
        called.append("sfdc")
        return {}

    def rec_podcast(**_k):
        called.append("podcast")
        return {}

    async def rec_linkedin(**_k):
        called.append("linkedin")
        return {"stats": {}}

    monkeypatch.setenv("PODCAST_CSV_URL", "https://example.test/pod.csv")
    monkeypatch.setattr(_app.engagement_sync_mod, "run_sync", rec_replyio)
    monkeypatch.setattr(_app.engagement_sync_mod, "run_sfdc_sync", rec_sfdc)
    monkeypatch.setattr(_app.engagement_sync_mod, "run_podcast_url_sync", rec_podcast)
    import auto_search.engagement.linkedin_ads_runner as _li
    monkeypatch.setattr(_li, "run", rec_linkedin)

    assert client.post("/api/engagement/sync").json()["started"] is True
    # drain the background job (it flips engagement_running off in its finally)
    for _ in range(100):
        if not client.app.state.engagement_running:
            break
        time.sleep(0.05)
    assert client.app.state.engagement_running is False
    assert set(called) == {"replyio", "sfdc", "podcast", "linkedin"}
