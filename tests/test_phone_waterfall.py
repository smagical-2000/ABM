"""Phone-resolution waterfall: cost-ordered, first-hit-wins, never raises."""
import pytest

from auto_search.engagement import phone_waterfall as pw


async def _sfdc_hit(email):
    return "+1 555 000 1111"


async def _sfdc_miss(email):
    return None


async def _sfdc_boom(email):
    raise RuntimeError("sfdc down")


@pytest.fixture(autouse=True)
def _no_real_fullenrich(monkeypatch):
    """Default: FullEnrich returns nothing. Tests opt in to a hit."""
    async def none(**kw):
        return {"email": None, "phone": None}
    monkeypatch.setattr(pw.enrichment, "enrich_contact", none)


async def test_tier1_apollo_wins_and_skips_paid(monkeypatch):
    """Apollo phone in hand → return it, never touch SFDC or FullEnrich."""
    called = []
    async def fe(**kw):
        called.append(1)
        return {"phone": "+1999"}
    monkeypatch.setattr(pw.enrichment, "enrich_contact", fe)
    phone, src, attempted = await pw.resolve_phone(first_name="A", last_name="B",
                                        email="a@b.com", apollo_phone="+1 415 000",
                                        sfdc_lookup=_sfdc_hit)
    assert (phone, src, attempted) == ("+1 415 000", "apollo", False)
    assert called == []                       # no paid call


async def test_tier2_salesforce_before_fullenrich(monkeypatch):
    """Apollo missed → Salesforce hit wins, FullEnrich never runs."""
    called = []
    async def fe(**kw):
        called.append(1)
        return {"phone": "+1999"}
    monkeypatch.setattr(pw.enrichment, "enrich_contact", fe)
    phone, src, attempted = await pw.resolve_phone(first_name="A", last_name="B",
                                        email="a@b.com", apollo_phone=None,
                                        sfdc_lookup=_sfdc_hit)
    assert (phone, src, attempted) == ("+1 555 000 1111", "salesforce", False)
    assert called == []


async def test_tier3_fullenrich_last_resort(monkeypatch):
    """Apollo + Salesforce miss → FullEnrich resolves it."""
    async def fe(**kw):
        return {"phone": "+1 650 777 8888"}
    monkeypatch.setattr(pw.enrichment, "enrich_contact", fe)
    phone, src, attempted = await pw.resolve_phone(first_name="A", last_name="B",
                                        email="a@b.com", apollo_phone=None,
                                        sfdc_lookup=_sfdc_miss)
    assert (phone, src, attempted) == ("+1 650 777 8888", "fullenrich", True)


async def test_allow_fullenrich_false_bounds_paid_tier(monkeypatch):
    """When the per-run cap is hit (allow_fullenrich=False) the paid tier is
    skipped entirely, even though it would have found a number."""
    called = []
    async def fe(**kw):
        called.append(1)
        return {"phone": "+1999"}
    monkeypatch.setattr(pw.enrichment, "enrich_contact", fe)
    phone, src, attempted = await pw.resolve_phone(first_name="A", last_name="B",
                                        email="a@b.com", apollo_phone=None,
                                        sfdc_lookup=_sfdc_miss, allow_fullenrich=False)
    assert (phone, src, attempted) == (None, None, False)   # capped -> NOT billed
    assert called == []


async def test_sfdc_error_is_swallowed_falls_through(monkeypatch):
    """A Salesforce blip must never drop the lead — the waterfall falls through
    to FullEnrich instead of raising."""
    async def fe(**kw):
        return {"phone": "+1 111 222 3333"}
    monkeypatch.setattr(pw.enrichment, "enrich_contact", fe)
    phone, src, attempted = await pw.resolve_phone(first_name="A", last_name="B",
                                        email="a@b.com", apollo_phone=None,
                                        sfdc_lookup=_sfdc_boom)
    assert (phone, src, attempted) == ("+1 111 222 3333", "fullenrich", True)


async def test_no_key_no_paid_call(monkeypatch):
    """No email and no LinkedIn → FullEnrich can't key the person → skip it."""
    called = []
    async def fe(**kw):
        called.append(1)
        return {"phone": "+1999"}
    monkeypatch.setattr(pw.enrichment, "enrich_contact", fe)
    phone, src, attempted = await pw.resolve_phone(first_name="A", last_name="B",
                                        email=None, linkedin=None, apollo_phone=None)
    assert (phone, src, attempted) == (None, None, False)   # unkeyable -> no bill
    assert called == []


async def test_all_miss_returns_none():
    phone, src, attempted = await pw.resolve_phone(first_name="A", last_name="B",
                                        email="a@b.com", apollo_phone=None,
                                        sfdc_lookup=_sfdc_miss)
    # a MISS still reports attempted=True — the lookup was billed (QA 2026-07-09:
    # the caller must cap attempts, not hits, or a run of misses spends unbounded)
    assert (phone, src, attempted) == (None, None, True)
