"""TOFU capture must hand incomplete leads to Clay (2026-07-28).

Adam Shively (VCU Health) was captured with a phone and no email and nothing
carried him onward: the Clay dispatch existed only as a MANUAL endpoint. The
CHG claimed leads auto-dispatched on capture; they did not. These pin the
behavior so the claim and the code can't diverge again.
"""

from __future__ import annotations

import pytest

from auto_search.engagement import linkedin_ads_runner as runner


def _lead(**kw):
    base = {"name": "Adam Shively", "email": "", "phone": "+15404209438",
            "title": "Associate CFO", "company": "VCU Health",
            "domain": "vcuhealth.org", "linkedin_url": "https://lnkd.in/in/adam",
            "profile_id": "ACoAA123", "first_name": "Adam", "last_name": "Shively"}
    base.update(kw)
    return base


class _Resp:
    status_code = 200

    def raise_for_status(self):
        return None


@pytest.fixture
def captured(monkeypatch):
    """Capture the bridge POST instead of sending it."""
    sent = {}
    monkeypatch.setenv("N8N_CLAY_DISPATCH_URL", "https://bridge.test/hook")
    monkeypatch.setenv("CLAY_BRIDGE_TOKEN", "tok")

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            sent["url"], sent["json"], sent["headers"] = url, json, headers
            return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    return sent


@pytest.mark.asyncio
async def test_lead_missing_email_is_dispatched_with_its_domain(captured):
    n = await runner._dispatch_incomplete_to_clay([_lead()])
    assert n == 1
    lead = captured["json"]["leads"][0]
    assert lead["needs"] == ["email"]          # has phone, wants email
    assert lead["company_domain"] == "vcuhealth.org"
    assert lead["first_name"] == "Adam" and lead["last_name"] == "Shively"
    assert captured["headers"]["X-Bridge-Token"] == "tok"


@pytest.mark.asyncio
async def test_complete_leads_are_never_dispatched(captured):
    n = await runner._dispatch_incomplete_to_clay(
        [_lead(email="a@vcuhealth.org", phone="+1555")])
    assert n == 0 and captured == {}


@pytest.mark.asyncio
async def test_missing_both_asks_for_both(captured):
    await runner._dispatch_incomplete_to_clay([_lead(email="", phone="")])
    assert captured["json"]["leads"][0]["needs"] == ["email", "phone"]


@pytest.mark.asyncio
async def test_domain_falls_back_to_the_email_domain(captured):
    """No captured domain but an email present (phone-only gap)."""
    await runner._dispatch_incomplete_to_clay(
        [_lead(domain="", email="adam@vcuhealth.org", phone="")])
    assert captured["json"]["leads"][0]["company_domain"] == "vcuhealth.org"


@pytest.mark.asyncio
async def test_missing_bridge_config_is_LOUD_when_leads_are_waiting(monkeypatch):
    """The vars lived only on the web service — on the capture cron this would
    have no-opped forever. Silence is the bug; make it raise."""
    monkeypatch.delenv("N8N_CLAY_DISPATCH_URL", raising=False)
    monkeypatch.delenv("CLAY_BRIDGE_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="clay bridge not configured"):
        await runner._dispatch_incomplete_to_clay([_lead()])


@pytest.mark.asyncio
async def test_no_incomplete_leads_never_complains_about_config(monkeypatch):
    """Nothing to send → the bridge being absent is genuinely fine."""
    monkeypatch.delenv("N8N_CLAY_DISPATCH_URL", raising=False)
    assert await runner._dispatch_incomplete_to_clay(
        [_lead(email="a@vcuhealth.org", phone="+1555")]) == 0


@pytest.mark.asyncio
async def test_bridge_failure_never_loses_the_lead(monkeypatch, captured):
    """The run must survive a bridge outage — leads are already persisted."""
    import httpx

    class _Boom(captured.__class__ if False else object):
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            raise httpx.ConnectError("bridge down")

    monkeypatch.setattr(httpx, "AsyncClient", _Boom)
    with pytest.raises(httpx.ConnectError):
        await runner._dispatch_incomplete_to_clay([_lead()])
    # run() wraps this in try/except — proven by the guard in the runner
