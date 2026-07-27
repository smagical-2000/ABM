"""A capped/blocked upstream account must be LOUD, never an empty result.

2026-07-27: the Apify account hit its monthly usage hard limit and returned
`403 {"error": {"type": "platform-feature-disabled", "message": "Monthly usage
hard limit exceeded"}}` on EVERY actor call — jobs boards, all three SignalBase
feeds, social listening and TOFU capture. Every one of those paths caught the
error and returned `[]`, so connector_runs stamped `success`, run_discovery
exited 0 and run_daily printed "all legs OK". A total upstream outage read as a
quiet market for three days.

These tests pin the whole chain: the two clients raise a distinct
UpstreamQuotaError, no connector/poller swallows it, and the discovery runner
turns the failures into ONE consolidated failure-severity ops alert.

No live calls anywhere — httpx.MockTransport and fakes only.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from auto_search.clients import apify_jobs, signalbase
from auto_search.clients.upstream import UpstreamError, UpstreamQuotaError
from auto_search.connectors.job_postings import JobPostingsConnector
from auto_search.social import apify as social_apify
from auto_search.social import poll as social_poll
from auto_search.social.models import SocialTarget

NOW = datetime.now(UTC)

# The exact body Apify serves a capped account (captured 2026-07-27).
QUOTA_BODY = {"error": {"type": "platform-feature-disabled",
                        "message": "Monthly usage hard limit exceeded"}}


def _client(status: int, body: object) -> httpx.AsyncClient:
    """An AsyncClient whose every request answers `status`/`body`."""
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ── the two clients ───────────────────────────────────────────────────


class TestApifyJobsClient:
    async def test_quota_403_raises_instead_of_empty_list(self):
        async with _client(403, QUOTA_BODY) as http:
            c = apify_jobs.ApifyJobsClient(token="t", http=http)
            with pytest.raises(UpstreamQuotaError, match="hard limit"):
                await c.search_indeed('"medical coder"')

    async def test_linkedin_path_raises_too(self):
        async with _client(403, QUOTA_BODY) as http:
            c = apify_jobs.ApifyJobsClient(token="t", http=http)
            with pytest.raises(UpstreamQuotaError):
                await c.search_linkedin("medical coder")

    async def test_auth_failure_raises_non_quota_upstream_error(self):
        async with _client(401, {"error": {"type": "token-not-found",
                                           "message": "Auth token is not valid"}}) as http:
            c = apify_jobs.ApifyJobsClient(token="t", http=http)
            with pytest.raises(UpstreamError) as ei:
                await c.search_indeed("x")
            assert not isinstance(ei.value, UpstreamQuotaError)

    async def test_healthy_empty_page_is_still_empty(self):
        async with _client(200, []) as http:
            c = apify_jobs.ApifyJobsClient(token="t", http=http)
            assert await c.search_indeed("x") == []


class TestSignalBaseClient:
    async def _pull(self, http):
        c = signalbase.SignalBaseClient(api_token="t", http=http)
        return [r async for r in c.iter_job_changes(per_page=5, max_pages=1)]

    async def test_quota_403_raises(self):
        async with _client(403, QUOTA_BODY) as http:
            with pytest.raises(UpstreamQuotaError, match="hard limit"):
                await self._pull(http)

    async def test_body_without_data_key_raises(self):
        # The 'credits: ?' tell: a body with no `data` key is not a SignalBase
        # response at all, and must never read as "0 records".
        async with _client(200, {"message": "something else"}) as http:
            with pytest.raises(UpstreamError):
                await self._pull(http)

    async def test_healthy_empty_page_is_still_empty(self):
        async with _client(200, {"success": True, "data": [],
                                 "meta": {"creditsUsed": 0}}) as http:
            assert await self._pull(http) == []


# ── social actor path ─────────────────────────────────────────────────


class TestSocialApify:
    async def test_quota_raises_a_quota_subclass(self, monkeypatch):
        monkeypatch.setenv("APIFY_API_KEY", "t")
        async with _client(403, QUOTA_BODY) as http:
            with pytest.raises(social_apify.ApifyQuotaExceeded):
                await social_apify.fetch_engagers(["https://linkedin.com/in/x"],
                                                  client=http)

    def test_quota_error_is_both_apify_and_upstream_quota(self):
        assert issubclass(social_apify.ApifyQuotaExceeded, social_apify.ApifyError)
        assert issubclass(social_apify.ApifyQuotaExceeded, UpstreamQuotaError)


# ── the swallow points ────────────────────────────────────────────────


class TestNoSwallowPoints:
    async def test_jobs_connector_gather_reraises_quota(self):
        class _Capped:
            async def search_indeed(self, *a, **kw):
                raise UpstreamQuotaError("apify: Monthly usage hard limit exceeded")

            async def search_linkedin(self, *a, **kw):
                raise UpstreamQuotaError("apify: Monthly usage hard limit exceeded")

        conn = JobPostingsConnector(client=_Capped(), max_rows=1)
        with pytest.raises(UpstreamQuotaError):
            async for _ in conn.pull(since=NOW - timedelta(days=1)):
                pass

    async def test_jobs_connector_still_tolerates_one_flaky_board(self):
        class _Flaky:
            async def search_indeed(self, *a, **kw):
                raise TimeoutError("indeed slow")

            async def search_linkedin(self, *a, **kw):
                return []

        conn = JobPostingsConnector(client=_Flaky(), max_rows=1)
        assert [s async for s in conn.pull(since=NOW - timedelta(days=1))] == []

    async def test_social_poll_targets_reraises_quota(self):
        async def _capped(*a, **kw):
            raise social_apify.ApifyQuotaExceeded("Monthly usage hard limit exceeded")

        targets = [SocialTarget(linkedin_url="https://x/company/rival",
                                label="Rival", kind="competitor", active=True)]
        with pytest.raises(UpstreamQuotaError):
            await social_poll.poll_targets(targets, repo=object(), fetch_fn=_capped)

    async def test_social_poll_targets_still_tolerates_a_plain_actor_error(self):
        async def _broken(*a, **kw):
            raise social_apify.ApifyError("actor run failed")

        targets = [SocialTarget(linkedin_url="https://x/company/rival",
                                label="Rival", kind="competitor", active=True)]
        summary = await social_poll.poll_targets(targets, repo=object(), fetch_fn=_broken)
        assert summary["engagers"] == 0

    async def test_tofu_scrape_reraises_quota(self, monkeypatch):
        from auto_search.engagement import linkedin_ads_runner

        async def _capped(*a, **kw):
            raise social_apify.ApifyQuotaExceeded("Monthly usage hard limit exceeded")

        monkeypatch.setattr(linkedin_ads_runner.social_apify,
                            "fetch_post_reactions", _capped)
        with pytest.raises(UpstreamQuotaError):
            await linkedin_ads_runner._scrape({"123": "cat"}, max_reactions=5)


# ── the consolidated ops alert ────────────────────────────────────────

_SPEC = importlib.util.spec_from_file_location(
    "run_discovery",
    Path(__file__).resolve().parent.parent / "scripts" / "run_discovery.py")
rd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rd)


class _FakeStateRepo:
    def __init__(self):
        self._kv: dict[str, str] = {}

    def get_setting(self, key):
        return self._kv.get(key)

    def set_setting(self, key, value):
        self._kv[key] = value


class TestConsolidatedFailureAlert:
    def test_quota_failures_post_one_failure_alert_with_runbook(self, monkeypatch):
        calls: list[dict] = []
        monkeypatch.setattr(rd, "post_ops_alert", lambda **kw: calls.append(kw) or True)
        state = _FakeStateRepo()

        rd.alert_failed_sources(
            {"jobs": "UpstreamQuotaError: Monthly usage hard limit exceeded",
             "leadership": "UpstreamQuotaError: Monthly usage hard limit exceeded",
             "funding": "UpstreamQuotaError: Monthly usage hard limit exceeded"},
            state_repo=state)

        assert len(calls) == 1                       # ONE alert for ALL sources
        kw = calls[0]
        assert kw["severity"] == "failure"
        assert kw["runbook"]                         # tells the reader what to do
        for src in ("jobs", "leadership", "funding"):
            assert src in kw["detail"]
        assert "quota" in (kw["kind"] + kw["title"] + kw["runbook"]).lower()

        # Throttled: a second call inside the gap posts nothing.
        rd.alert_failed_sources({"jobs": "UpstreamQuotaError: capped"}, state_repo=state)
        assert len(calls) == 1

    def test_non_quota_failure_still_alerts_at_failure_severity(self, monkeypatch):
        # warntracker's stale-feed tripwire: a connector that RAISES must page as
        # a failure, not only as the throttled 24h source-silence warning.
        calls: list[dict] = []
        monkeypatch.setattr(rd, "post_ops_alert", lambda **kw: calls.append(kw) or True)
        rd.alert_failed_sources(
            {"layoffs": "RuntimeError: warntracker feed stale: newest notice 2026-04-27"},
            state_repo=_FakeStateRepo())
        assert len(calls) == 1
        assert calls[0]["severity"] == "failure"
        assert "warntracker" in calls[0]["detail"]

    def test_no_failures_posts_nothing(self, monkeypatch):
        calls: list[dict] = []
        monkeypatch.setattr(rd, "post_ops_alert", lambda **kw: calls.append(kw) or True)
        rd.alert_failed_sources({}, state_repo=_FakeStateRepo())
        assert calls == []
