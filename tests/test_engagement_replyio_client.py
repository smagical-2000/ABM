"""Reply.io v3 client (Milestone B) — pagination, the read-only report query,
429 backoff, and auth. All mocked via httpx.MockTransport — no network.
"""

import json as _json

import httpx
import pytest

from auto_search.engagement.replyio_client import ReplyioClient, default_window


def _client(handler):
    """A ReplyioClient wired to a mock transport that runs `handler(request)`."""
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ReplyioClient(api_key="test-key", http=http), http


@pytest.mark.asyncio
async def test_iter_contacts_paginates_and_authenticates():
    pages = [
        {"items": [{"id": 1}, {"id": 2}], "hasMore": True},
        {"items": [{"id": 3}], "hasMore": False},
    ]
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["Authorization"] == "Bearer test-key"   # Bearer auth
        assert req.method == "GET"
        assert req.url.path.endswith("/v3/contacts")
        seen.append(dict(req.url.params))
        return httpx.Response(200, json=pages[len(seen) - 1])

    client, http = _client(handler)
    try:
        got = [c async for c in client.iter_contacts(top=2)]
    finally:
        await http.aclose()
    assert [c["id"] for c in got] == [1, 2, 3]
    assert seen[0]["skip"] == "0" and seen[1]["skip"] == "2"      # skip advances by page size


@pytest.mark.asyncio
async def test_email_activity_is_a_read_only_report_query():
    bodies = []

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "POST"                                # report query
        assert req.url.path.endswith("/v3/reporting/emails")
        bodies.append(_json.loads(req.content))
        return httpx.Response(200, json={"items": [{"contactId": 7, "isReplied": True}],
                                         "hasMore": False})

    client, http = _client(handler)
    try:
        frm, to = default_window(30)
        rows = [r async for r in client.iter_email_activity(date_from=frm, date_to=to)]
    finally:
        await http.aclose()
    assert rows == [{"contactId": 7, "isReplied": True}]
    # window sent as bare dates (Reply.io 500s on offset datetimes — regression below)
    assert bodies[0]["filters"]["from"] == frm.date().isoformat()
    assert bodies[0]["filters"]["to"] == to.date().isoformat()


@pytest.mark.asyncio
async def test_backoff_then_success_on_429(monkeypatch):
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("auto_search.engagement.replyio_client.asyncio.sleep", fake_sleep)
    responses = [
        httpx.Response(429, headers={"Retry-After": "0.01"}, json={}),
        httpx.Response(200, json={"items": [{"id": 1}], "hasMore": False}),
    ]

    def handler(req):
        return responses.pop(0)

    client, http = _client(handler)
    try:
        got = [c async for c in client.iter_contacts()]
    finally:
        await http.aclose()
    assert [c["id"] for c in got] == [1]
    assert slept == [0.01]               # backed off once, honoring Retry-After


@pytest.mark.asyncio
async def test_4xx_raises_without_retry():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(400, json={"detail": "bad"})

    client, http = _client(handler)
    try:
        with pytest.raises(httpx.HTTPStatusError):
            [c async for c in client.iter_contacts()]
    finally:
        await http.aclose()
    assert len(calls) == 1               # a 400 is not retried


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("REPLYIO_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        ReplyioClient(api_key=None)


def test_default_window_is_30_days_by_default():
    frm, to = default_window()
    assert 29 <= (to - frm).days <= 30


@pytest.mark.asyncio
async def test_reporting_window_is_date_only_not_offset_datetime():
    """Regression (replyio-reporting-500-on-offset-datetime): Reply.io's
    /reporting/emails returns 500 on a tz-offset datetime like '...+00:00'. We
    must send a bare 'YYYY-MM-DD' date even when given an aware datetime."""
    from datetime import UTC, datetime

    captured = {}

    def handler(req):
        captured.update(_json.loads(req.content)["filters"])
        return httpx.Response(200, json={"items": [], "hasMore": False})

    client, http = _client(handler)
    try:
        _ = [r async for r in client.iter_email_activity(
            date_from=datetime(2026, 5, 15, 3, 4, 5, tzinfo=UTC),
            date_to=datetime(2026, 6, 14, 9, 9, 9, tzinfo=UTC))]
    finally:
        await http.aclose()
    assert captured["from"] == "2026-05-15"
    assert captured["to"] == "2026-06-14"
    assert "T" not in captured["from"] and "+" not in captured["from"]
