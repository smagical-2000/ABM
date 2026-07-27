"""Route-layer clamping of `limit` / `days` query params.

Live QA (2026-07-27) reached three defects through the same unvalidated int:
  · GET /api/news?limit=-1            -> 500 (Postgres rejects a negative LIMIT)
  · GET /api/engagement/inbox?limit=-1 -> 500 (same raw-LIMIT class)
  · GET /api/ops/changelog?limit=0    -> 1 entry, because of a max(1, limit)

Contract: 0 <= limit <= a sane ceiling, clamped (not 422 — no client passes one
today and a silent 500 is the thing being removed), and limit=0 means an EMPTY
list, not "one" and not "all".
"""

from __future__ import annotations

import importlib
import json

import pytest
from fastapi.testclient import TestClient

_app = importlib.import_module("auto_search.api.app")


@pytest.fixture
def client(tmp_path, monkeypatch):
    from auto_search.db.engagement_repository import EngagementJsonRepository
    from auto_search.db.repository import JsonFileRepository
    from auto_search.db.scoring_repository import ScoringJsonRepository

    for var in ("BASIC_AUTH_USER", "BASIC_AUTH_PASS", "DATABASE_URL"):
        monkeypatch.delenv(var, raising=False)
    repo = JsonFileRepository(tmp_path / "s.json")
    repo.save_news_items([
        {"url": f"https://n/{i}", "title": f"Headline {i}", "topic": "rcm",
         "relevant": True, "get_behind": i,
         "published_at": f"2026-07-{i + 10:02d}T00:00:00+00:00"}
        for i in range(4)])
    eng = EngagementJsonRepository(tmp_path / "eng.json")
    for i in range(4):
        eng.add_event({"source": "replyio", "external_id": f"e{i}",
                       "channel": "email", "kind": "reply", "points": 6,
                       "contact_ext": "c1", "company": "Acme",
                       "account_id": "abm_acme",
                       "occurred_at": f"2026-07-{i + 10:02d}T00:00:00+00:00"})
    eng.set_setting("automation_changelog", json.dumps(
        [{"change_id": f"c{i}", "what": f"change {i}"} for i in range(4)]))
    monkeypatch.setattr(_app, "get_repository", lambda: repo)
    monkeypatch.setattr(_app, "get_scoring_repository",
                        lambda: ScoringJsonRepository(tmp_path / "sc.json"))
    monkeypatch.setattr(_app, "get_engagement_repository", lambda: eng)
    with TestClient(_app.create_app()) as c:
        yield c


# ── the helper ───────────────────────────────────────────────────────────


def test_clamp_limit_bounds():
    clamp = _app._clamp_limit
    assert clamp(-1, 10) == 0            # never negative -> never a SQL error
    assert clamp(0, 10) == 0             # an explicit zero stays zero
    assert clamp(3, 10) == 3
    assert clamp(9_999, 10) == 10        # ceiling, not an unbounded dump


# ── /api/news ────────────────────────────────────────────────────────────


def test_news_negative_limit_is_empty_not_an_error(client):
    r = client.get("/api/news?limit=-1")
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_news_zero_limit_is_empty(client):
    assert client.get("/api/news?limit=0").json()["items"] == []


def test_news_negative_days_is_not_an_error(client):
    r = client.get("/api/news?days=-1")
    assert r.status_code == 200


def test_news_normal_limit_still_pages(client):
    assert len(client.get("/api/news?limit=2").json()["items"]) == 2


# ── /api/engagement/inbox ────────────────────────────────────────────────


def test_inbox_negative_limit_is_empty_not_an_error(client):
    r = client.get("/api/engagement/inbox?limit=-1")
    assert r.status_code == 200
    assert r.json()["events"] == []


def test_inbox_zero_limit_is_empty(client):
    assert client.get("/api/engagement/inbox?limit=0").json()["events"] == []


def test_inbox_normal_limit_still_pages(client):
    assert len(client.get("/api/engagement/inbox?limit=2").json()["events"]) == 2


# ── /api/ops/changelog ───────────────────────────────────────────────────


def test_changelog_zero_limit_returns_no_entries(client):
    body = client.get("/api/ops/changelog?limit=0").json()
    assert body["entries"] == []         # was 1: max(1, limit) coercion
    assert body["total"] == 4            # total still reports the whole log


def test_changelog_negative_limit_returns_no_entries(client):
    assert client.get("/api/ops/changelog?limit=-1").json()["entries"] == []


def test_changelog_normal_limit_still_pages_newest_first(client):
    entries = client.get("/api/ops/changelog?limit=2").json()["entries"]
    assert [e["change_id"] for e in entries] == ["c3", "c2"]
