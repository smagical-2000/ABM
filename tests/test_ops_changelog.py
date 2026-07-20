"""Automation change log — pure card, safe poster, and the endpoints
(create initiated -> complete with the same change_id -> list). Slack mocked."""

import importlib
import json

import pytest
from fastapi.testclient import TestClient

from auto_search.ops import changelog

_app_module = importlib.import_module("auto_search.api.app")


# ── pure card + entry validation ──────────────────────────────────────


def test_entry_requires_what_and_known_status():
    with pytest.raises(ValueError):
        changelog.ChangeEntry(what="   ")
    with pytest.raises(ValueError):
        changelog.ChangeEntry(what="x", status="wip")
    e = changelog.ChangeEntry(what="Cadence 6h -> 15min", status="INITIATED")
    assert e.status == "initiated" and e.change_id.startswith("chg_")


def test_card_uses_company_announcement_format():
    """What? / Why? / Who? / When? — the company Feature-Announcement style.
    'Who?' is the AUDIENCE, not the author (author rides in the footer with
    the serial, the GitHub link, and the Notion register link)."""
    e = changelog.ChangeEntry(
        what="LinkedIn checks now every 15 minutes in selling hours",
        why="Likes must reach Slack fast; the hours gate keeps cost ~$2.7/day",
        area="cadence_scheduling", who="Sunny", audience="SDRs and AEs",
        status="completed", summary="A like is picked up within 15 minutes",
        serial=14, github="https://github.com/getmagical/abm-discovery/pull/30")
    payload = changelog.build_change_card(e)
    card = json.dumps(payload)
    for needle in ("*What?*", "*Why?*", "*Who?*", "*When?*",
                   "SDRs and AEs", "Live now", "Cadence or scheduling",
                   "by Sunny", "CHG-14",
                   "<https://github.com/getmagical/abm-discovery/pull/30|GitHub>",
                   "full history (Notion)"):
        assert needle in card
    assert payload["blocks"][0]["text"]["text"].startswith("CHG-14 · ")
    assert "automation_logic" not in card and "cadence_scheduling" not in card
    assert not any(0x1F300 <= ord(ch) <= 0x1FAFF for ch in card)   # no emoji
    started = json.dumps(changelog.build_change_card(
        changelog.ChangeEntry(what="x", status="initiated")))
    assert "In progress" in started
    assert "CHG-" not in started                         # serial 0 = no tag
    default_aud = json.dumps(changelog.build_change_card(changelog.ChangeEntry(what="x")))
    assert "GTM team" in default_aud                     # audience never blank


def test_poster_without_webhook_is_safe(monkeypatch):
    monkeypatch.delenv("SLACK_CHANGELOG_WEBHOOK", raising=False)
    assert changelog.post_change(changelog.ChangeEntry(what="x")) is False


def test_poster_failure_never_raises(monkeypatch):
    monkeypatch.setenv("SLACK_CHANGELOG_WEBHOOK", "https://hooks.slack.example/x")

    def boom(*_a, **_k):
        raise RuntimeError("slack down")
    monkeypatch.setattr(changelog.httpx, "post", boom)
    assert changelog.post_change(changelog.ChangeEntry(what="x")) is False


# ── Notion mirror ─────────────────────────────────────────────────────


def test_notion_properties_map_the_schema():
    e = changelog.ChangeEntry(
        what="Hot reactivation rule", why="Galyna: re-alert on new activity",
        area="automation_logic", who="Sunny", status="completed",
        summary="ledger stores tier+touch")
    p = changelog.notion_properties(e)
    assert p["What changed"]["title"][0]["text"]["content"] == "Hot reactivation rule"
    assert p["Status"]["select"]["name"] == "Completed"        # label, not "completed"
    assert p["Area"]["select"]["name"] == "Automation logic"   # label, not the key
    assert p["Why it changed"]["rich_text"][0]["text"]["content"].startswith("Galyna")
    assert p["Change ref"]["rich_text"][0]["text"]["content"] == e.change_id
    assert p["When implemented"]["date"]["start"] == e.created_at


def test_notion_omits_empty_optionals():
    p = changelog.notion_properties(changelog.ChangeEntry(what="x"))
    assert "Why it changed" not in p and "Summary" not in p   # nothing empty written
    assert "Serial" not in p and "GitHub" not in p            # unset -> omitted


def test_notion_maps_serial_and_github():
    p = changelog.notion_properties(changelog.ChangeEntry(
        what="x", serial=7,
        github="https://github.com/getmagical/abm-discovery/commit/abc1234"))
    assert p["Serial"] == {"number": 7}
    assert p["GitHub"] == {"url": "https://github.com/getmagical/abm-discovery/commit/abc1234"}


def test_notion_post_needs_token_and_db(monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.delenv("NOTION_CHANGELOG_DB_ID", raising=False)

    def boom(*_a, **_k):
        raise AssertionError("must not call Notion without token+db")
    monkeypatch.setattr(changelog.httpx, "post", boom)
    assert changelog.post_to_notion(changelog.ChangeEntry(what="x")) is False


def test_notion_post_success_and_failure(monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "secret_x")
    monkeypatch.setenv("NOTION_CHANGELOG_DB_ID", "db123")
    seen = {}

    class _R:
        def raise_for_status(self):
            pass

    def ok(url, timeout=None, headers=None, json=None):
        seen.update(url=url, parent=json["parent"], hdr=headers["Notion-Version"])
        return _R()
    monkeypatch.setattr(changelog.httpx, "post", ok)
    assert changelog.post_to_notion(changelog.ChangeEntry(what="x")) is True
    assert seen["parent"] == {"database_id": "db123"} and seen["hdr"] == "2022-06-28"

    def boom(*_a, **_k):
        raise RuntimeError("notion down")
    monkeypatch.setattr(changelog.httpx, "post", boom)
    assert changelog.post_to_notion(changelog.ChangeEntry(what="x")) is False


# ── endpoints ─────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path, monkeypatch):
    from auto_search.db.engagement_repository import EngagementJsonRepository
    from auto_search.db.repository import JsonFileRepository
    from auto_search.db.scoring_repository import ScoringJsonRepository

    monkeypatch.delenv("BASIC_AUTH_USER", raising=False)
    monkeypatch.delenv("BASIC_AUTH_PASS", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SLACK_CHANGELOG_WEBHOOK", raising=False)
    monkeypatch.setattr(_app_module, "get_repository",
                        lambda: JsonFileRepository(tmp_path / "s.json"))
    monkeypatch.setattr(_app_module, "get_scoring_repository",
                        lambda: ScoringJsonRepository(tmp_path / "sc.json"))
    # Hermetic: without this the app boots the ENGAGEMENT repo on its default
    # local JSON file and changelog entries leak across test runs.
    monkeypatch.setattr(_app_module, "get_engagement_repository",
                        lambda: EngagementJsonRepository(tmp_path / "eng.json"))
    with TestClient(_app_module.create_app()) as c:
        yield c


def test_initiate_complete_and_list(client, monkeypatch):
    posts = []
    monkeypatch.setattr(changelog, "post_change", lambda e, **k: posts.append(e) or True)

    first = client.post("/api/ops/changelog", json={
        "what": "Hot reactivation rule", "why": "Galyna: Hot re-alerts on new activity",
        "area": "automation_logic", "who": "Sunny", "status": "initiated"}).json()
    assert first["slack_posted"] is True
    cid = first["entry"]["change_id"]

    done = client.post("/api/ops/changelog", json={
        "change_id": cid, "what": "Hot reactivation rule", "status": "completed",
        "summary": "Deployed; seed baselined 822 accounts; dry-run due=0"}).json()
    assert done["entry"]["change_id"] == cid and done["total"] == 2
    assert [e.status for e in posts] == ["initiated", "completed"]

    # Serials: the first change gets CHG-1; its completed post INHERITS 1 (one
    # number per change, not per post); the next new change gets CHG-2.
    assert first["entry"]["serial"] == 1 and done["entry"]["serial"] == 1
    third = client.post("/api/ops/changelog", json={
        "what": "Another change",
        "github": "https://github.com/getmagical/abm-discovery/pull/31"}).json()
    assert third["entry"]["serial"] == 2
    assert third["entry"]["github"].endswith("/pull/31")

    lst = client.get("/api/ops/changelog").json()
    assert lst["total"] == 3
    assert lst["entries"][0]["serial"] == 2                 # newest first


def test_add_validates(client):
    assert client.post("/api/ops/changelog", json={"why": "no what"}).status_code == 422
    assert client.post("/api/ops/changelog",
                       json={"what": "x", "status": "wip"}).status_code == 422
