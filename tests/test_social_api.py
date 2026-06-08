"""POST /api/social/trigify — webhook auth, filtering, and panel surfacing.

Forces the JSON repos (no Postgres) and injects a fake qualifier so the real
endpoint + ingest wiring is exercised without an LLM call. Mirrors test_abm_api.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

from auto_search.db.repository import JsonFileRepository
from auto_search.models import QualificationResult

_app_module = importlib.import_module("auto_search.api.app")

_SECRET = "twh_test_secret"


async def _fake_qualify(signal):
    return QualificationResult(
        qualified=True, confidence=0.9, reasoning="fake",
        segment="health_system", domain="mercy.example", decided_by="llm")


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = tmp_path / "discovery.json"
    monkeypatch.delenv("BASIC_AUTH_USER", raising=False)
    monkeypatch.delenv("BASIC_AUTH_PASS", raising=False)
    monkeypatch.setenv("TRIGIFY_WEBHOOK_SECRET", _SECRET)
    monkeypatch.setattr(_app_module, "get_repository", lambda: JsonFileRepository(store))
    from auto_search.db.scoring_repository import ScoringJsonRepository
    monkeypatch.setattr(_app_module, "get_scoring_repository",
                        lambda: ScoringJsonRepository(tmp_path / "scoring.json"))

    # Exercise the real ingest (incl. the cost-cap gate), but with a fake
    # (no-LLM) qualifier. Forward op/can_qualify so the budget guard still runs.
    real_ingest = _app_module.ingest_engager

    async def _ingest(engager, *, repo, op=None, can_qualify=None):
        return await real_ingest(engager, repo=repo, qualify_fn=_fake_qualify,
                                 op=op, can_qualify=can_qualify)
    monkeypatch.setattr(_app_module, "ingest_engager", _ingest)

    from auto_search.api.app import create_app
    with TestClient(create_app()) as c:
        yield c


def _dm():
    return {"full_name": "Jane Doe", "job_title": "VP Revenue Cycle",
            "company_name": "Mercy Health", "source": "magical_post",
            "linkedin_url": "https://www.linkedin.com/in/janedoe",
            "post_url": "https://www.linkedin.com/feed/update/urn:li:activity:1"}


def test_missing_secret_is_unconfigured(client, monkeypatch):
    monkeypatch.delenv("TRIGIFY_WEBHOOK_SECRET", raising=False)
    r = client.post("/api/social/trigify", json=_dm())
    assert r.status_code == 503


def test_wrong_secret_rejected(client):
    r = client.post("/api/social/trigify", json=_dm(),
                    headers={"X-Trigify-Secret": "nope"})
    assert r.status_code == 401


def test_decision_maker_ingested_and_panelled(client):
    r = client.post("/api/social/trigify", json=_dm(),
                    headers={"X-Trigify-Secret": _SECRET})
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] == 1 and body["results"][0]["action"] == "qualified"

    panel = {c["name"]: c for c in client.get("/api/panel").json()}
    assert "Mercy Health" in panel
    sig = panel["Mercy Health"]["signals"][0]
    assert sig["signal_type"] == "social_engagement"
    assert sig["person_name"] == "Jane Doe"


def test_non_decision_maker_skipped(client):
    rec = {**_dm(), "job_title": "Billing Specialist"}
    r = client.post("/api/social/trigify", json=rec,
                    headers={"X-Trigify-Secret": _SECRET})
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] == 0 and body["results"][0]["reason"] == "not_decision_maker"


def test_per_request_qualify_cap_is_enforced(client, monkeypatch):
    # Cap new qualifications at 2; a batch of 3 distinct new companies should
    # qualify 2 and refuse the 3rd with reason 'request_cap' (no LLM spent on it).
    monkeypatch.setenv("SOCIAL_WEBHOOK_MAX_QUALIFY", "2")
    batch = {"engagers": [
        {**_dm(), "company_name": "Mercy Health",
         "linkedin_url": "https://www.linkedin.com/in/a"},
        {**_dm(), "company_name": "Bryan Health",
         "linkedin_url": "https://www.linkedin.com/in/b"},
        {**_dm(), "company_name": "Centra Health",
         "linkedin_url": "https://www.linkedin.com/in/c"},
    ]}
    r = client.post("/api/social/trigify", json=batch,
                    headers={"X-Trigify-Secret": _SECRET})
    assert r.status_code == 200
    body = r.json()
    assert body["qualified_new"] == 2
    reasons = [res["reason"] for res in body["results"]]
    assert reasons.count("request_cap") == 1


def test_batch_with_one_bad_record_does_not_500(client):
    payload = {"engagers": [_dm(), {"job_title": "no name or company"}]}
    r = client.post("/api/social/trigify", json=payload,
                    headers={"X-Trigify-Secret": _SECRET})
    assert r.status_code == 200
    body = r.json()
    assert body["received"] == 2
    assert body["accepted"] == 1
    # the malformed record is reported, not fatal
    assert any(not res["accepted"] for res in body["results"])


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
