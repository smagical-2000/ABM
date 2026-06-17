"""Contact enrichment — Apollo decision-makers + FullEnrich email/phone. Mocked
(no network, no credits): Apollo is monkeypatched, FullEnrich uses an injected
fake async client."""

from __future__ import annotations

import pytest

from auto_search.engagement import enrichment


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._p = payload or {}

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _FakeFE:
    """Minimal async httpx stand-in: POST -> enrichment_id, GET -> FINISHED + data."""
    def __init__(self, eid, result):
        self._eid, self._result = eid, result
        self.posted = None

    async def post(self, url, json=None, headers=None):
        self.posted = json
        return _Resp(200, {"enrichment_id": self._eid})

    async def get(self, url, headers=None):
        return _Resp(200, {"status": "FINISHED", "data": self._result})

    async def aclose(self):
        pass


@pytest.mark.asyncio
async def test_enrich_merges_apollo_and_fullenrich(monkeypatch):
    async def fake_dms(domain):
        return [{"name": "Jane Doe", "title": "VP Revenue Cycle", "linkedin": "li/jane"},
                {"name": "John Roe", "title": "CFO", "linkedin": "li/john"}]
    monkeypatch.setattr(enrichment.apollo, "decision_makers", fake_dms)
    monkeypatch.setenv("FULLENRICH_API_KEY", "k")
    fe = _FakeFE("eid-1", [
        {"contact_info": {"most_probable_work_email": {"email": "jane@acme.com"},
                          "most_probable_phone": {"number": "+1 555 0001"}}},
        {"contact_info": {"work_emails": [{"email": "john@acme.com"}],
                          "phones": [{"number": "+1 555 0002"}]}},
    ])
    out = await enrichment.enrich_account("acme.com", company="Acme", http=fe)
    assert [p["name"] for p in out] == ["Jane Doe", "John Roe"]
    assert out[0]["email"] == "jane@acme.com" and out[0]["phone"] == "+1 555 0001"
    assert out[1]["email"] == "john@acme.com" and out[1]["phone"] == "+1 555 0002"
    # the FullEnrich payload split the name + carried the domain
    assert fe.posted["data"][0]["first_name"] == "Jane" and fe.posted["data"][0]["last_name"] == "Doe"
    assert fe.posted["data"][0]["domain"] == "acme.com"


@pytest.mark.asyncio
async def test_enrich_without_key_returns_apollo_only(monkeypatch):
    async def fake_dms(domain):
        return [{"name": "Jane", "title": "CFO", "linkedin": "x"}]
    monkeypatch.setattr(enrichment.apollo, "decision_makers", fake_dms)
    monkeypatch.delenv("FULLENRICH_API_KEY", raising=False)
    out = await enrichment.enrich_account("acme.com")
    assert out == [{"name": "Jane", "title": "CFO", "linkedin": "x", "email": None, "phone": None}]


@pytest.mark.asyncio
async def test_enrich_no_decision_makers(monkeypatch):
    async def empty(domain):
        return []
    monkeypatch.setattr(enrichment.apollo, "decision_makers", empty)
    assert await enrichment.enrich_account("acme.com") == []


def test_merge_uses_custom_ref_and_tolerates_null_entries():
    # FullEnrich returns rows reordered + one null — must still map to the right person
    dms = [{"name": "A"}, {"name": "B"}, {"name": "C"}]
    data = [
        None,                                                              # junk/partial
        {"custom": {"ref": "2"}, "contact_info": {"most_probable_work_email": {"email": "c@x.com"}}},
        {"custom": {"ref": "0"}, "contact_info": {"phones": [{"number": "+1 0"}]}},
    ]
    out = enrichment._merge(dms, data)
    assert out[0]["phone"] == "+1 0" and out[0]["email"] is None   # ref 0 → A
    assert out[1]["email"] is None and out[1]["phone"] is None     # B: no row
    assert out[2]["email"] == "c@x.com"                            # ref 2 → C


def test_merge_positional_fallback_when_no_ref():
    dms = [{"name": "A"}, {"name": "B"}]
    data = [{"contact_info": {"most_probable_work_email": {"email": "a@x.com"},
                              "most_probable_phone": {"number": "+1"}}}, None]
    out = enrichment._merge(dms, data)
    assert out[0]["email"] == "a@x.com" and out[0]["phone"] == "+1"
    assert out[1]["email"] is None and out[1]["phone"] is None     # null entry safe
