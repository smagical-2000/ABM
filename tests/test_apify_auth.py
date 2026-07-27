"""The Apify API token must never travel in a URL.

httpx logs every request at INFO ("HTTP Request: POST <full url> ..."), and the
Apify actor endpoints used to carry the key as `?token=<APIFY_API_KEY>`. The
discovery-cron and social legs therefore printed the live key dozens of times
per run into Railway's log store — anyone with log access held the Apify key
(observed in the Jul-24 and Jul-27 executions). Apify accepts
`Authorization: Bearer <token>`, so the key belongs in a header, and the cron
entrypoints silence httpx INFO as defence in depth.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import httpx
import pytest

from auto_search.clients import apify_jobs, signalbase
from auto_search.ops.logsetup import quiet_http_logs
from auto_search.social import apify as social_apify

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
TOKEN = "apify_api_SUPERSECRET"


class _Recorder:
    """Captures every outgoing request so we can assert on the wire format."""

    def __init__(self, body: object):
        self.requests: list[httpx.Request] = []
        self._body = body

    def client(self) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(200, json=self._body)
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def assert_bearer_and_no_query_token(self):
        assert self.requests, "no request captured"
        for r in self.requests:
            assert TOKEN not in str(r.url), f"token leaked into the URL: {r.url}"
            assert r.headers.get("authorization") == f"Bearer {TOKEN}"


class TestBearerAuth:
    async def test_apify_jobs_client(self):
        rec = _Recorder([])
        async with rec.client() as http:
            await apify_jobs.ApifyJobsClient(token=TOKEN, http=http).search_indeed("x")
        rec.assert_bearer_and_no_query_token()

    async def test_signalbase_client(self):
        rec = _Recorder({"success": True, "data": [], "meta": {"creditsUsed": 0}})
        async with rec.client() as http:
            c = signalbase.SignalBaseClient(api_token=TOKEN, http=http)
            assert [r async for r in c.iter_job_changes(per_page=1, max_pages=1)] == []
        rec.assert_bearer_and_no_query_token()

    async def test_social_actor_calls(self, monkeypatch):
        monkeypatch.setenv("APIFY_API_KEY", TOKEN)
        rec = _Recorder([])
        async with rec.client() as http:
            await social_apify.fetch_engagers(["https://linkedin.com/in/x"], client=http)
            await social_apify.fetch_post_reactions("https://linkedin.com/feed/x",
                                                    client=http)
            await social_apify.enrich("https://linkedin.com/in/x", client=http)
            await social_apify.search_event_posts(["HIMSS26"], client=http)
        assert len(rec.requests) == 4
        rec.assert_bearer_and_no_query_token()


class TestQuietHttpLogs:
    def test_silences_the_request_loggers(self):
        for name in ("httpx", "httpcore"):
            logging.getLogger(name).setLevel(logging.INFO)
        quiet_http_logs()
        for name in ("httpx", "httpcore", "anthropic"):
            assert logging.getLogger(name).level == logging.WARNING

    @pytest.mark.parametrize("script", ["run_discovery.py", "run_social.py",
                                        "run_linkedin_tofu.py"])
    def test_apify_touching_cron_entrypoints_silence_httpx(self, script):
        """Defence in depth: any leg that can reach Apify must not log requests
        at INFO. A new cron leg added without this fails here."""
        src = (_SCRIPTS / script).read_text()
        assert re.search(r"quiet_http_logs\s*\(", src), (
            f"{script} must call quiet_http_logs() before it makes HTTP calls")
