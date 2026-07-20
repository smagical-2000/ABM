"""CMS MSSP ACO enrichment — matching rules (generic words never match), fact
formatting (no emails/phones), caching, and fetch failure degradation."""

import pytest

from auto_search.clients import cms_aco


@pytest.fixture(autouse=True)
def _clean_cache():
    cms_aco._cache.update(at=0.0, rows=[])
    yield
    cms_aco._cache.update(at=0.0, rows=[])


def _aco(name, **kw):
    base = {"aco_id": "A0001", "aco_name": name, "aco_service_area": "LA",
            "initial_start_date": "01/01/2016", "enhanced_track": "1",
            "basic_track": "0", "basic_track_level": "N/A",
            "aco_exec_name": "Jim Stelzer", "aco_exec_email": "secret@x.com",
            "aco_exec_phone": "(520) 555-0000",
            "aco_medical_director_name": "Melissa Levine"}
    base.update(kw)
    return base


ACOS = [
    _aco("Ochsner Accountable Care Network"),
    _aco("Abacus Health LLC", aco_id="A2811"),
    _aco("Community Health Partners ACO"),
]


def test_containment_match():
    m = cms_aco.match_acos("Ochsner Health System", ACOS)
    assert [a["aco_name"] for a in m] == ["Ochsner Accountable Care Network"]


def test_generic_words_never_match_alone():
    # "health", "partners", "community", "care" are all stop tokens.
    assert cms_aco.match_acos("Community Health Partners of Ohio", ACOS) == [] or \
        all("Community" not in a["aco_name"] for a in
            cms_aco.match_acos("Community Health of Nowhere", ACOS))
    assert cms_aco.match_acos("Valley Care Health Network", ACOS) == []


def test_two_significant_tokens_match():
    acos = [_aco("Sacred Heart Quality Alliance")]
    assert cms_aco.match_acos("Ascension Sacred Heart Health System", acos) == acos


def test_facts_have_people_but_never_contact_details():
    cms_aco._cache.update(at=9e12, rows=[ACOS[0]])
    facts = cms_aco.aco_known_facts("Ochsner Health")
    (label, line), = facts.items()
    assert "ACO participation" in label
    assert "Enhanced track" in line and "Jim Stelzer" in line
    assert "secret@x.com" not in line and "555" not in line


def test_no_match_returns_empty():
    cms_aco._cache.update(at=9e12, rows=ACOS)
    assert cms_aco.aco_known_facts("Stripe") == {}


def test_fetch_pages_and_caches(monkeypatch):
    calls = []

    class _R:
        def __init__(self, js):
            self._js = js

        def raise_for_status(self):
            pass

        def json(self):
            return self._js

    def fake_get(url, params=None, timeout=None):
        calls.append(params["offset"])
        return _R([_aco(f"ACO {params['offset']}")])   # short page ends the loop

    monkeypatch.setattr(cms_aco.httpx, "get", fake_get)
    rows = cms_aco.fetch_acos()
    assert len(rows) == 1 and calls == [0]
    assert cms_aco.fetch_acos() is rows                # cached, no second call
    assert calls == [0]


def test_fetch_failure_degrades_to_stale_or_empty(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("cms down")
    monkeypatch.setattr(cms_aco.httpx, "get", boom)
    assert cms_aco.fetch_acos() == []                  # no cache -> empty, no raise
    cms_aco._cache.update(at=0.0, rows=[_aco("Stale ACO")])
    assert cms_aco.fetch_acos()[0]["aco_name"] == "Stale ACO"   # stale beats nothing
