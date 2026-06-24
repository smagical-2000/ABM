"""Airtable write client — upsert/create request shape, record-id extraction,
429 backoff, auth, config. All mocked via httpx.MockTransport — no network.
"""

import json as _json

import httpx
import pytest

from auto_search.engagement.airtable_client import AirtableClient


def _client(handler):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return AirtableClient(base_id="appTEST", table="tblTEST", api_key="key-123",
                          http=http), http


@pytest.mark.asyncio
async def test_upsert_updates_existing_match_by_id():
    """upsert finds the row by Email (GET) then UPDATES the first match by record id
    (PATCH) — no performUpsert (which 422s on duplicate rows)."""
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if req.method == "GET":
            assert "filterByFormula" in req.url.query.decode()
            return httpx.Response(200, json={"records": [{"id": "recABC"}]})
        return httpx.Response(200, json={"records": [{"id": "recABC", "fields": {}}]})

    client, http = _client(handler)
    try:
        res = await client.upsert({"Email": "a@b.com", "Company Name": "X"},
                                  merge_on=["Email"])
    finally:
        await http.aclose()
    assert [c.method for c in calls] == ["GET", "PATCH"]
    patch = _json.loads(calls[1].content)
    assert "performUpsert" not in patch                 # never the brittle native upsert
    assert patch["records"][0]["id"] == "recABC"        # update by id
    assert patch["records"][0]["fields"]["Email"] == "a@b.com"
    assert patch["typecast"] is True
    assert calls[1].headers["Authorization"] == "Bearer key-123"
    assert AirtableClient.record_id(res) == "recABC"


@pytest.mark.asyncio
async def test_upsert_creates_when_no_match():
    """No existing row → CREATE (POST)."""
    calls = []

    def handler(req):
        calls.append(req)
        if req.method == "GET":
            return httpx.Response(200, json={"records": []})
        return httpx.Response(200, json={"records": [{"id": "recNEW"}]})

    client, http = _client(handler)
    try:
        res = await client.upsert({"Email": "z@b.com", "Company Name": "X"}, merge_on=["Email"])
    finally:
        await http.aclose()
    assert [c.method for c in calls] == ["GET", "POST"]
    assert "id" not in _json.loads(calls[1].content)["records"][0]   # create, no id
    assert AirtableClient.record_id(res) == "recNEW"


@pytest.mark.asyncio
async def test_upsert_tolerates_duplicate_rows():
    """The bug guard: when the table already has DUPLICATE rows for the email (e.g. from
    a Clay workflow), upsert must still succeed — it updates the first match, never 422s."""
    def handler(req):
        if req.method == "GET":   # two rows match the email — would break performUpsert
            return httpx.Response(200, json={"records": [{"id": "recDUP1"}, {"id": "recDUP2"}]})
        return httpx.Response(200, json={"records": [{"id": "recDUP1", "fields": {}}]})

    client, http = _client(handler)
    try:
        res = await client.upsert({"Email": "dup@b.com", "Company Name": "X"}, merge_on=["Email"])
    finally:
        await http.aclose()
    assert AirtableClient.record_id(res) == "recDUP1"    # updated first dup, no 422


@pytest.mark.asyncio
async def test_create_uses_post_without_upsert():
    captured = {}

    def handler(req):
        captured["method"] = req.method
        captured["body"] = _json.loads(req.content)
        return httpx.Response(200, json={"records": [{"id": "recNEW"}]})

    client, http = _client(handler)
    try:
        res = await client.create({"Email": "c@d.com"})
    finally:
        await http.aclose()
    assert captured["method"] == "POST"
    assert "performUpsert" not in captured["body"]
    assert AirtableClient.record_id(res) == "recNEW"


@pytest.mark.asyncio
async def test_backoff_then_success_on_429(monkeypatch):
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("auto_search.engagement.airtable_client.asyncio.sleep", fake_sleep)
    responses = [httpx.Response(429, headers={"Retry-After": "0.01"}, json={}),
                 httpx.Response(200, json={"records": [{"id": "recOK"}]})]

    def handler(req):
        return responses.pop(0)

    client, http = _client(handler)
    try:
        res = await client.create({"Email": "e@f.com"})   # single POST → clean 429 path
    finally:
        await http.aclose()
    assert AirtableClient.record_id(res) == "recOK"
    assert slept == [0.01]


@pytest.mark.asyncio
async def test_4xx_raises_without_retry():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(422, json={"error": "INVALID"})

    client, http = _client(handler)
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await client.upsert({"Email": "g@h.com"}, merge_on=["Email"])
    finally:
        await http.aclose()
    assert len(calls) == 1               # a 422 is not retried


def test_missing_config_raises(monkeypatch):
    monkeypatch.delenv("AIRTABLE_BASE_ID", raising=False)
    monkeypatch.delenv("AIRTABLE_LINKEDIN_TABLE", raising=False)
    monkeypatch.setenv("AIRTABLE_API_KEY", "k")
    with pytest.raises(RuntimeError):
        AirtableClient()


def test_missing_key_raises(monkeypatch):
    monkeypatch.setenv("AIRTABLE_BASE_ID", "appX")
    monkeypatch.setenv("AIRTABLE_LINKEDIN_TABLE", "tblX")
    monkeypatch.delenv("AIRTABLE_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        AirtableClient()
