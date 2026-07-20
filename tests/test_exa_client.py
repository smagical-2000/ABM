"""Exa client — parse, ceilings, and failure modes. No network: httpx is patched."""

import pytest

from auto_search.clients import exa


class _Resp:
    def __init__(self, js, status=200):
        self._js, self.status_code = js, status

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("boom", request=None, response=self)

    def json(self):
        return self._js


def test_domain_of_normalizes():
    assert exa.domain_of("https://www.ivyrehab.com/about/") == "ivyrehab.com"
    assert exa.domain_of("ivyrehab.com") == "ivyrehab.com"
    assert exa.domain_of("http://CORA.com:443/x") == "cora.com"
    assert exa.domain_of("not a url") == ""
    assert exa.domain_of(None) == ""


def test_search_parses_results(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "k")
    seen = {}

    def fake_post(url, json=None, timeout=None, headers=None):
        seen.update(json)
        return _Resp({"results": [
            {"title": "Ivy Rehab", "url": "https://www.ivyrehab.com/",
             "text": "x" * 5000, "publishedDate": "2026-01-01"},
            "garbage-not-a-dict",
        ]})

    monkeypatch.setattr(exa.httpx, "post", fake_post)
    out = exa.search("ivy rehab", num_results=3)
    assert len(out) == 1
    assert out[0].domain == "ivyrehab.com"
    assert len(out[0].text) <= 1200          # snippet is bounded
    assert seen["numResults"] == 3


def test_num_results_hard_ceiling(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "k")
    seen = {}

    def fake_post(url, json=None, timeout=None, headers=None):
        seen.update(json)
        return _Resp({"results": []})

    monkeypatch.setattr(exa.httpx, "post", fake_post)
    exa.search("q", num_results=999)
    assert seen["numResults"] == exa.MAX_RESULTS


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    with pytest.raises(exa.ExaError, match="EXA_API_KEY"):
        exa.search("q")


def test_http_error_raises_exa_error(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "k")
    monkeypatch.setattr(exa.httpx, "post",
                        lambda *a, **k: _Resp({}, status=401))
    with pytest.raises(exa.ExaError, match="401"):
        exa.search("q")


def test_malformed_body_raises(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "k")
    monkeypatch.setattr(exa.httpx, "post", lambda *a, **k: _Resp({"nope": 1}))
    with pytest.raises(exa.ExaError, match="no results"):
        exa.search("q")


def test_search_cost():
    assert exa.search_cost(5) == 0.01
    assert exa.search_cost(0) == 0.005
