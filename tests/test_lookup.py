"""AE one-off lookup — resolve outcomes + the code-level confidence gate.
No network: Exa + the identity LLM pass are injected/patched fakes. The gate
is the accuracy contract: anything not clearly ONE ICP company must come back
as a non-'new' status so the AE explicitly chooses."""

import pytest

from auto_search.clients.exa import ExaError, ExaResult
from auto_search.scoring import lookup


def _r(title, url, text=""):
    return ExaResult(title=title, url=url, domain=lookup.exa.domain_of(url), text=text)


class _PanelCompany:
    def __init__(self, key, name, status="qualified", signals=(1, 2)):
        self.company_key, self.name = key, name
        self.icp_status, self.signals = status, list(signals)
        self.segment = "specialty"


def _ident(monkeypatch, payload):
    async def fake(_name, _domain, _results):
        return payload, 0.01
    monkeypatch.setattr(lookup, "_identify", fake)


_GOOD_IDENT = {
    "matched": True, "name": "Ivy Rehab Network", "domain": "ivyrehab.com",
    "segment": "specialty", "sub_segment": "Physical Therapy",
    "confidence": "high", "description": "Outpatient PT network.",
    "hq": "White Plains, NY", "approximate_employees": 2000,
    "evidence_url": "https://ivyrehab.com/", "reason": "Official site matches.",
}


# ── question 1: existing data wins, no spend ──────────────────────────


@pytest.mark.asyncio
async def test_already_scored_by_domain_short_circuits(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("must not search when the account exists")
    monkeypatch.setattr(lookup, "_identify", boom)
    accounts = [{"account_id": "acc_ivyrehab", "name": "Ivy Rehab",
                 "domain": "ivyrehab.com", "state": "scored", "tier_label": "Tier 1"}]
    out = await lookup.resolve("IVY Rehab Network", "https://www.ivyrehab.com/x",
                               accounts=accounts, get_company=lambda k: None,
                               search=boom)
    assert out["status"] == "already_scored"
    assert out["account_id"] == "acc_ivyrehab"


@pytest.mark.asyncio
async def test_already_scored_by_normalized_name():
    accounts = [{"account_id": "csv_x", "name": "CORA Physical Therapy, LLC",
                 "domain": None, "state": "scored"}]
    out = await lookup.resolve("cora physical therapy", None,
                               accounts=accounts, get_company=lambda k: None,
                               search=lambda *a, **k: [])
    assert out["status"] == "already_scored"


@pytest.mark.asyncio
async def test_live_discovery_company_offers_promote():
    comp = _PanelCompany("orthoindy", "OrthoIndy")
    out = await lookup.resolve("OrthoIndy", None, accounts=[],
                               get_company=lambda k: comp if k == "orthoindy" else None,
                               search=lambda *a, **k: [])
    assert out["status"] == "in_discovery"
    assert out["company"]["signals"] == 2


@pytest.mark.asyncio
async def test_rejected_discovery_company_is_not_offered(monkeypatch):
    comp = _PanelCompany("nope", "Nope Co", status="rejected")
    _ident(monkeypatch, _GOOD_IDENT)
    out = await lookup.resolve("Nope Co", None, accounts=[],
                               get_company=lambda k: comp,
                               search=lambda *a, **k: [_r("Ivy", "https://ivyrehab.com")])
    assert out["status"] == "new"          # fell through to web resolve


# ── question 2: the gate ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_new_company_high_confidence_resolves(monkeypatch):
    _ident(monkeypatch, _GOOD_IDENT)
    out = await lookup.resolve("ivy rehab", "ivyrehab.com", accounts=[],
                               get_company=lambda k: None,
                               search=lambda *a, **k: [_r("Ivy", "https://ivyrehab.com")])
    assert out["status"] == "new"
    assert out["resolved"]["domain"] == "ivyrehab.com"
    assert out["resolved"]["segment"] == "specialty"
    assert out["cost_usd"] > 0


@pytest.mark.asyncio
async def test_low_confidence_never_proceeds(monkeypatch):
    _ident(monkeypatch, {**_GOOD_IDENT, "confidence": "low"})
    out = await lookup.resolve("ivy", None, accounts=[], get_company=lambda k: None,
                               search=lambda *a, **k: [_r("x", "https://x.com")])
    assert out["status"] == "unresolved"


@pytest.mark.asyncio
async def test_domain_disagreement_is_ambiguous_never_silent(monkeypatch):
    _ident(monkeypatch, {**_GOOD_IDENT, "domain": "ivyrehab.org"})
    out = await lookup.resolve("ivy rehab", "ivyrehab.com", accounts=[],
                               get_company=lambda k: None,
                               search=lambda *a, **k: [_r("x", "https://ivyrehab.org")])
    assert out["status"] == "ambiguous"    # AE must choose, we never pick


@pytest.mark.asyncio
async def test_non_icp_is_flagged(monkeypatch):
    _ident(monkeypatch, {**_GOOD_IDENT, "segment": "non_icp"})
    out = await lookup.resolve("Stripe", None, accounts=[], get_company=lambda k: None,
                               search=lambda *a, **k: [_r("Stripe", "https://stripe.com")])
    assert out["status"] == "non_icp"


@pytest.mark.asyncio
async def test_unknown_segment_is_unresolved(monkeypatch):
    _ident(monkeypatch, {**_GOOD_IDENT, "segment": "hospital"})   # not a bucket
    out = await lookup.resolve("x", None, accounts=[], get_company=lambda k: None,
                               search=lambda *a, **k: [_r("x", "https://x.com")])
    assert out["status"] == "unresolved"


@pytest.mark.asyncio
async def test_exa_failure_degrades_to_unresolved():
    def broken(*_a, **_k):
        raise ExaError("quota")
    out = await lookup.resolve("x", None, accounts=[], get_company=lambda k: None,
                               search=broken)
    assert out["status"] == "unresolved"
    assert "quota" in out["error"]


@pytest.mark.asyncio
async def test_identity_pass_failure_degrades_to_unresolved(monkeypatch):
    async def boom(*_a, **_k):
        raise RuntimeError("claude down")
    monkeypatch.setattr(lookup, "_identify", boom)
    out = await lookup.resolve("x", None, accounts=[], get_company=lambda k: None,
                               search=lambda *a, **k: [_r("x", "https://x.com")])
    assert out["status"] == "unresolved"


@pytest.mark.asyncio
async def test_empty_name_raises():
    with pytest.raises(ValueError):
        await lookup.resolve("  ", None, accounts=[], get_company=lambda k: None)


# ── question 3: build_account ─────────────────────────────────────────


def test_build_account_shape():
    a = lookup.build_account(name="Ivy Rehab Network", domain="www.ivyrehab.com",
                             segment="specialty", sub_segment="Physical Therapy",
                             description="Outpatient PT network.",
                             hq="White Plains, NY",
                             evidence_url="https://ivyrehab.com/",
                             approximate_employees=2000)
    assert a.account_id == "acc_ivyrehabnetwork"   # same key scheme as discovery
    assert a.source == "ae"
    assert a.domain == "ivyrehab.com"
    assert a.framework == "specialty"
    assert a.firmographics["Company website"] == "ivyrehab.com"
    assert "Net Patient Revenue" not in a.firmographics   # identity facts ONLY


def test_build_account_rejects_bad_segment_and_domain():
    with pytest.raises(ValueError, match="segment"):
        lookup.build_account(name="X", domain="x.com", segment="non_icp")
    with pytest.raises(ValueError, match="domain"):
        lookup.build_account(name="X", domain="not a domain", segment="payer")
