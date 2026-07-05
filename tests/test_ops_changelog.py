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


def test_card_uses_ticket_structure_plain_language_no_emoji():
    """The card must read for a NON-TECHNICAL teammate, with Galyna's exact
    field structure and status wording."""
    e = changelog.ChangeEntry(
        what="LinkedIn TOFU cadence 6h -> 15min (selling hours)",
        why="Likes must reach Slack fast; hours gate keeps Apify cost ~$2.7/day",
        area="cadence_scheduling", who="Sunny", status="completed",
        summary="15-min scans weekdays 9-7 ET; revert = one env flag")
    card = json.dumps(changelog.build_change_card(e))
    for needle in ("Change completed", "What changed:", "Why it changed:",
                   "Who made the change:", "When it was implemented:",
                   "Status:* Completed", "Cadence or scheduling", e.change_id):
        assert needle in card
    assert "automation_logic" not in card and "cadence_scheduling" not in card
    assert not any(0x1F300 <= ord(ch) <= 0x1FAFF for ch in card)   # no emoji
    started = json.dumps(changelog.build_change_card(
        changelog.ChangeEntry(what="x", status="initiated")))
    assert "Change started" in started and "In progress" in started


def test_poster_without_webhook_is_safe(monkeypatch):
    monkeypatch.delenv("SLACK_CHANGELOG_WEBHOOK", raising=False)
    assert changelog.post_change(changelog.ChangeEntry(what="x")) is False


def test_poster_failure_never_raises(monkeypatch):
    monkeypatch.setenv("SLACK_CHANGELOG_WEBHOOK", "https://hooks.slack.example/x")

    def boom(*_a, **_k):
        raise RuntimeError("slack down")
    monkeypatch.setattr(changelog.httpx, "post", boom)
    assert changelog.post_change(changelog.ChangeEntry(what="x")) is False


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

    lst = client.get("/api/ops/changelog").json()
    assert lst["total"] == 2
    assert lst["entries"][0]["status"] == "completed"       # newest first


def test_add_validates(client):
    assert client.post("/api/ops/changelog", json={"why": "no what"}).status_code == 422
    assert client.post("/api/ops/changelog",
                       json={"what": "x", "status": "wip"}).status_code == 422
