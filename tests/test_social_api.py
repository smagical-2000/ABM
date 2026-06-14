"""Monitored-accounts + event-keyword API (the Apify social-listening config).

Forces the JSON repos (no Postgres) so the real endpoints + repo wiring are
exercised without infrastructure. The engagement scrape itself is Apify-driven
and covered in test_social_apify.py / the poll tests.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

from auto_search.db.repository import JsonFileRepository

_app_module = importlib.import_module("auto_search.api.app")


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = tmp_path / "discovery.json"
    monkeypatch.delenv("BASIC_AUTH_USER", raising=False)
    monkeypatch.delenv("BASIC_AUTH_PASS", raising=False)
    monkeypatch.setattr(_app_module, "get_repository", lambda: JsonFileRepository(store))
    from auto_search.db.scoring_repository import ScoringJsonRepository
    monkeypatch.setattr(_app_module, "get_scoring_repository",
                        lambda: ScoringJsonRepository(tmp_path / "scoring.json"))

    from auto_search.api.app import create_app
    with TestClient(create_app()) as c:
        yield c


# ── monitored-accounts API ───────────────────────────────────────────────────

def test_magical_is_seeded_as_own(client):
    targets = client.get("/api/social/targets").json()["targets"]
    own = [t for t in targets if t["kind"] == "own"]
    assert any("getmagical" in t["linkedin_url"] for t in own)


def test_add_and_remove_competitor(client):
    add = client.post("/api/social/targets", json={
        "linkedin_url": "https://www.linkedin.com/company/acme-health", "label": "Acme"})
    assert add.status_code == 200 and add.json()["kind"] == "competitor"
    urls = [t["linkedin_url"] for t in client.get("/api/social/targets").json()["targets"]]
    assert any("acme-health" in u for u in urls)

    rm = client.request("DELETE", "/api/social/targets",
                        json={"linkedin_url": "https://www.linkedin.com/company/acme-health"})
    assert rm.status_code == 200 and rm.json()["removed"] is True


def test_cannot_remove_magical(client):
    rm = client.request("DELETE", "/api/social/targets",
                        json={"linkedin_url": "https://ca.linkedin.com/company/getmagical/"})
    assert rm.status_code == 400  # regional/trailing-slash variant still protected


def test_add_rejects_non_linkedin_url(client):
    assert client.post("/api/social/targets", json={"linkedin_url": "example.com"}).status_code == 400


def test_target_url_validation_rejects_lookalikes(client):
    # substring look-alike and bare host must both be rejected
    assert client.post("/api/social/targets", json={"linkedin_url": "https://evil.com/linkedin.com/x"}).status_code == 400
    assert client.post("/api/social/targets", json={"linkedin_url": "https://linkedin.com"}).status_code == 400
    # a real company URL is accepted
    assert client.post("/api/social/targets", json={"linkedin_url": "https://www.linkedin.com/company/real-co"}).status_code == 200


def test_event_keyword_crud(client):
    assert client.get("/api/social/keywords").json()["keywords"] == []
    assert client.post("/api/social/keywords", json={"keyword": '"HIMSS26"'}).status_code == 200
    # dedup is case/quote-insensitive
    client.post("/api/social/keywords", json={"keyword": "himss26"})
    kws = client.get("/api/social/keywords").json()["keywords"]
    assert len(kws) == 1
    assert client.post("/api/social/keywords", json={"keyword": "x"}).status_code == 400  # too short
    assert client.request("DELETE", "/api/social/keywords", json={"keyword": "HIMSS26"}).json()["removed"]
    assert client.get("/api/social/keywords").json()["keywords"] == []
