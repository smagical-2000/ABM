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
async def test_upsert_request_shape_and_record_id():
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["method"] = req.method
        captured["path"] = req.url.path
        captured["auth"] = req.headers["Authorization"]
        captured["body"] = _json.loads(req.content)
        return httpx.Response(200, json={"records": [{"id": "recABC", "fields": {}}]})

    client, http = _client(handler)
    try:
        res = await client.upsert({"Email": "a@b.com", "Company Name": "X"},
                                  merge_on=["Email"])
    finally:
        await http.aclose()
    assert captured["method"] == "PATCH"
    assert captured["path"] == "/v0/appTEST/tblTEST"
    assert captured["auth"] == "Bearer key-123"
    assert captured["body"]["performUpsert"]["fieldsToMergeOn"] == ["Email"]
    assert captured["body"]["records"][0]["fields"]["Email"] == "a@b.com"
    assert captured["body"]["typecast"] is True
    assert AirtableClient.record_id(res) == "recABC"


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
        res = await client.upsert({"Email": "e@f.com"}, merge_on=["Email"])
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
