"""Outreach dashboard aggregator — rate math, shaping, and channel isolation.

The aggregator is the accuracy layer between the executors' raw analytics and
the dashboard: rates recomputed from counts, string counts coerced, one failed
campaign (or one whole channel) never blanking the rest.
"""

from __future__ import annotations

import asyncio

from auto_search.campaigns import outreach_stats


def _run(coro):
    return asyncio.run(coro)


# ── fakes ────────────────────────────────────────────────────────────────


class FakeSmartlead:
    def __init__(self, campaigns, analytics, *, fail_ids=()):
        self._campaigns, self._analytics, self._fail = campaigns, analytics, set(fail_ids)

    async def list_campaigns(self):
        return self._campaigns

    async def campaign_analytics(self, cid):
        if cid in self._fail:
            raise RuntimeError(f"boom {cid}")
        return self._analytics[cid]


class FakeHeyreach:
    def __init__(self, overall, per_campaign, campaigns):
        self._overall, self._per, self._campaigns = overall, per_campaign, campaigns

    async def overall_stats(self, *, campaign_ids=None, account_ids=None,
                            start=None, end=None):
        if campaign_ids:
            return self._per[campaign_ids[0]]
        return self._overall

    async def list_campaigns(self, *, limit=100):
        return self._campaigns


SL_RAW = {
    1: {"id": 1, "name": "HS - Article", "status": "ACTIVE",
        "sent_count": "200", "unique_open_count": "120", "unique_click_count": "30",
        "reply_count": "10", "bounce_count": "4", "unsubscribed_count": "2",
        "campaign_lead_stats": {"total": 500, "interested": 6}},
    2: {"id": 2, "name": "Ortho - CEO", "status": "DRAFTED",
        "sent_count": "0", "unique_open_count": "0", "unique_click_count": "0",
        "reply_count": "0", "bounce_count": "0", "unsubscribed_count": "0",
        "campaign_lead_stats": {"total": 0, "interested": 0}},
}
SL_CAMPAIGNS = [{"id": 1, "name": "HS - Article", "status": "ACTIVE"},
                {"id": 2, "name": "Ortho - CEO", "status": "DRAFTED"}]

HR_OVERALL = {"overallStats": {"connectionsSent": 100, "connectionsAccepted": 40,
                               "messagesSent": 40, "totalMessageReplies": 10,
                               "inmailMessagesSent": 0, "totalInmailReplies": 0,
                               "profileViews": 7, "uniqueLeadsContacted": 90,
                               "autoTaggedInterested": 3},
              "byDayStats": {f"2026-07-{d:02d}": {"connectionsSent": d} for d in range(1, 14)}}
HR_CAMPAIGNS = [{"id": 505509, "name": "Health Systems - LinkedIn",
                 "status": "IN_PROGRESS", "senders": 3}]
HR_PER = {505509: {"overallStats": {"connectionsSent": 100, "connectionsAccepted": 40,
                                    "messagesSent": 40, "totalMessageReplies": 10},
                   "byDayStats": {}}}


# ── rate math ────────────────────────────────────────────────────────────


def test_rate_none_when_nothing_sent():
    assert outreach_stats._rate(0, 0) is None
    assert outreach_stats._rate(5, 0) is None


def test_rate_percent_rounded():
    assert outreach_stats._rate(1, 3) == 33.3
    assert outreach_stats._rate(120, 200) == 60.0


def test_num_coerces_smartlead_strings():
    assert outreach_stats._num("42") == 42
    assert outreach_stats._num(None) == 0
    assert outreach_stats._num("garbage") == 0


# ── email shaping ────────────────────────────────────────────────────────


def test_email_overall_and_rows():
    out = _run(outreach_stats.collect_email(FakeSmartlead(SL_CAMPAIGNS, SL_RAW)))
    assert out["configured"] is True
    assert out["overall"]["sent"] == 200
    assert out["overall"]["open_rate"] == 60.0
    assert out["overall"]["reply_rate"] == 5.0
    assert out["overall"]["interested"] == 6
    # active campaign sorts above the drafted zero-row
    assert [r["id"] for r in out["campaigns"]] == [1, 2]
    # a drafted campaign shows None rates, not fake zeros
    assert out["campaigns"][1]["open_rate"] is None


def test_email_one_campaign_failing_drops_only_that_row():
    sl = FakeSmartlead(SL_CAMPAIGNS, SL_RAW, fail_ids={2})
    out = _run(outreach_stats.collect_email(sl))
    assert out["campaigns_errored"] == 1
    assert [r["id"] for r in out["campaigns"]] == [1]
    assert out["overall"]["sent"] == 200


def test_email_unconfigured():
    assert _run(outreach_stats.collect_email(None)) == {"configured": False}


# ── linkedin shaping ─────────────────────────────────────────────────────


def test_linkedin_overall_rates_recomputed_from_counts():
    out = _run(outreach_stats.collect_linkedin(
        FakeHeyreach(HR_OVERALL, HR_PER, HR_CAMPAIGNS)))
    o = out["overall"]
    assert o["connections_sent"] == 100
    assert o["accept_rate"] == 40.0
    assert o["message_reply_rate"] == 25.0
    assert o["inmail_reply_rate"] is None          # nothing sent -> None
    assert out["campaigns"][0]["id"] == 505509
    assert out["campaigns"][0]["accept_rate"] == 40.0


def test_linkedin_trend_sorted_and_bounded():
    out = _run(outreach_stats.collect_linkedin(
        FakeHeyreach(HR_OVERALL, HR_PER, HR_CAMPAIGNS)))
    trend = out["trend"]
    assert trend[0]["date"] == "2026-07-01"
    assert trend[-1]["date"] == "2026-07-13"
    assert trend[-1]["connectionsSent"] == 13


def test_linkedin_unconfigured():
    assert _run(outreach_stats.collect_linkedin(None)) == {"configured": False}


# ── channel isolation ────────────────────────────────────────────────────


class ExplodingSmartlead:
    async def list_campaigns(self):
        raise RuntimeError("smartlead down")


def test_one_channel_failing_never_blanks_the_other():
    payload = _run(outreach_stats.collect(
        smartlead=ExplodingSmartlead(),
        heyreach=FakeHeyreach(HR_OVERALL, HR_PER, HR_CAMPAIGNS)))
    assert payload["email"]["configured"] is True
    assert "smartlead down" in payload["email"]["error"]
    assert payload["linkedin"]["overall"]["connections_sent"] == 100
    assert payload["fetched_at"]


# ── QA findings, 2026-07-14 (each fix guarded) ───────────────────────────


def test_err_redacts_api_key_in_urls():
    """BLOCKER guard: SmartLead auths via ?api_key= and httpx error strings
    embed the full URL — the key must never reach the payload/UI/logs."""
    e = RuntimeError("Client error '401 Unauthorized' for url "
                     "'https://server.smartlead.ai/api/v1/campaigns?api_key=SECRET123'")
    out = outreach_stats._err(e)
    assert "SECRET123" not in out
    assert "api_key=***" in out


def test_err_survives_leading_newlines():
    assert outreach_stats._err(RuntimeError("\nsecond line")) == "second line"
    assert outreach_stats._err(RuntimeError("")) == "RuntimeError"


class NonDictSmartlead:
    """API returns a JSON list body for one campaign's analytics."""
    async def list_campaigns(self):
        return SL_CAMPAIGNS

    async def campaign_analytics(self, cid):
        return ["not", "a", "dict"] if cid == 2 else SL_RAW[cid]


def test_email_non_dict_body_drops_row_not_channel():
    out = _run(outreach_stats.collect_email(NonDictSmartlead()))
    assert "error" not in out
    assert out["campaigns_errored"] == 1
    assert [r["id"] for r in out["campaigns"]] == [1]


class NonDictHeyreach(FakeHeyreach):
    async def overall_stats(self, *, campaign_ids=None, account_ids=None,
                            start=None, end=None):
        return []          # unexpected top-level shape


def test_linkedin_non_dict_overall_is_channel_error_not_crash():
    out = _run(outreach_stats.collect_linkedin(
        NonDictHeyreach(HR_OVERALL, HR_PER, HR_CAMPAIGNS)))
    assert out["configured"] is True
    assert "unexpected response shape" in out["error"]


def test_collect_belt_turns_escaped_exception_into_channel_error():
    """Even a collector-level escape must not 500 the endpoint."""
    class Hostile:
        def __getattr__(self, name):        # every method access detonates
            raise RuntimeError("total meltdown")
    payload = _run(outreach_stats.collect(
        smartlead=Hostile(),
        heyreach=FakeHeyreach(HR_OVERALL, HR_PER, HR_CAMPAIGNS)))
    assert payload["email"]["error"]
    assert payload["linkedin"]["overall"]["connections_sent"] == 100
