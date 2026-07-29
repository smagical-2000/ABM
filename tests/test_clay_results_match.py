"""Clay writeback matcher (MAR2-50 D): the results receiver matched NOTHING
for a row whose {LinkedIn URL} EXACTLY equaled the posted linkedin_url.

Adam Shively was dispatched to Clay BY LinkedIn URL (his row had no email —
that is why he was dispatched); Clay found the email and posted it back, and
the receiver then used the ENRICHED email as the match key — a value the row
cannot possibly hold yet — so filled:0 and a human patched the row by hand.

The matcher must try the keys we actually HOLD, strongest first: the echoed
record_id, the ORIGINAL email, then the LinkedIn URL with BOTH sides
normalized (normalize_linkedin_url: https/http, www., regional subdomains,
query strings, trailing slashes all collapse), and only then the enriched
email. And the accounting must balance: filled + skipped == received, an
unmatched result counting as skipped with a reason.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

_app_module = importlib.import_module("auto_search.api.app")

ADAM = "https://www.linkedin.com/in/adam-shively-12345"


class _FakeAT:
    instances: list = []

    def __init__(self, *a, **k):
        self.patched = []
        self.rows = {
            # dispatched-for-email row: LinkedIn URL, no Email (the Adam shape)
            "recADAM": {"First Name": "Adam", "Last Name": "Shively",
                        "LinkedIn URL": ADAM, "Email": "", "Phone": ""},
            # stored with www + trailing slash; results may post bare http
            "recVAR": {"First Name": "Vera", "LinkedIn URL":
                       "https://www.linkedin.com/in/vera-lopez/", "Email": ""},
            "recMAIL": {"First Name": "Mel", "Email": "mel@corp.com", "Phone": ""},
        }
        _FakeAT.instances.append(self)

    _url = "https://api.airtable.test/base/tbl"

    @property
    def _headers(self):
        return {"Authorization": "Bearer x"}

    async def _find_id(self, fields, merge_on):
        """Airtable-faithful: exact string equality on the field value."""
        key = merge_on[0]
        want = str(fields.get(key, ""))
        for rid, f in self.rows.items():
            if str(f.get(key, "")) == want and want:
                return rid
        return None

    async def records(self):
        return [{"id": rid, "fields": dict(f)} for rid, f in self.rows.items()]


@pytest.fixture
def client(tmp_path, monkeypatch):
    from auto_search.db.engagement_repository import EngagementJsonRepository
    from auto_search.db.repository import JsonFileRepository
    from auto_search.db.scoring_repository import ScoringJsonRepository
    for v in ("BASIC_AUTH_USER", "BASIC_AUTH_PASS", "DATABASE_URL",
              "AIRTABLE_TOFU_MIRROR_BASE_ID"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("CLAY_BRIDGE_TOKEN", "secret")
    monkeypatch.setattr(_app_module, "get_repository",
                        lambda: JsonFileRepository(tmp_path / "s.json"))
    monkeypatch.setattr(_app_module, "get_scoring_repository",
                        lambda: ScoringJsonRepository(tmp_path / "sc.json"))
    monkeypatch.setattr(_app_module, "get_engagement_repository",
                        lambda: EngagementJsonRepository(tmp_path / "e.json"))
    _FakeAT.instances = []
    monkeypatch.setattr("auto_search.engagement.airtable_client.AirtableClient",
                        _FakeAT)

    class _Resp:
        def __init__(self, data):
            self._d = data

        def raise_for_status(self):
            pass

        def json(self):
            return self._d

    class _HC:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def get(self, url, headers=None):
            rid = url.rsplit("/", 1)[-1]
            return _Resp({"fields": _FakeAT.instances[0].rows.get(rid, {})})

        async def patch(self, url, headers=None, json=None):
            rid = url.rsplit("/", 1)[-1]
            fat = _FakeAT.instances[0]
            fat.patched.append((rid, json["fields"]))
            fat.rows.setdefault(rid, {}).update(json["fields"])
            return _Resp({})

    monkeypatch.setattr("httpx.AsyncClient", _HC)
    return TestClient(_app_module.create_app())


def _post(client, results):
    r = client.post("/api/enrichment/clay/results",
                    headers={"X-Bridge-Token": "secret"},
                    json={"results": results})
    assert r.status_code == 200
    return r.json()


def test_exact_linkedin_url_fills_even_when_clay_found_an_email(client):
    """The Adam Shively shape: posted linkedin_url == stored {LinkedIn URL}
    byte-for-byte, enriched email present, row has no email yet."""
    body = _post(client, [{"linkedin_url": ADAM, "email": "adam@vcuhealth.org"}])
    assert body["filled"] == 1 and body["skipped"] == 0
    patched = dict(_FakeAT.instances[0].patched)
    assert patched["recADAM"] == {"Email": "adam@vcuhealth.org"}


def test_url_variants_still_match_after_normalization(client):
    """http, no www, no trailing slash vs stored https+www+slash."""
    body = _post(client, [{"linkedin_url": "http://linkedin.com/in/vera-lopez",
                           "email": "vera@corp.com"}])
    assert body["filled"] == 1
    patched = dict(_FakeAT.instances[0].patched)
    assert patched["recVAR"] == {"Email": "vera@corp.com"}


def test_query_string_and_regional_subdomain_variants_match(client):
    body = _post(client, [{
        "linkedin_url": "https://ca.linkedin.com/in/adam-shively-12345?utm=x",
        "phone": "+1 555"}])
    assert body["filled"] == 1
    assert dict(_FakeAT.instances[0].patched)["recADAM"] == {"Phone": "+1 555"}


def test_unmatched_result_counts_as_skipped_and_books_balance(client):
    body = _post(client, [
        {"linkedin_url": "https://linkedin.com/in/nobody-here", "email": "x@y.com"},
        {"linkedin_url": ADAM, "email": "adam@vcuhealth.org"},
        {"email": "found@nowhere.com"},      # enriched email only, no row holds it
    ])
    assert body["received"] == 3
    assert body["filled"] == 1 and body["skipped"] == 2
    assert body["filled"] + body["skipped"] == body["received"]
    assert body["skip_reasons"].get("no_row_matched") == 2


def test_original_email_still_matches_first(client):
    """match_email (the row's own email) keeps working and wins."""
    body = _post(client, [{"match_email": "mel@corp.com", "phone": "+1 777"}])
    assert body["filled"] == 1
    assert dict(_FakeAT.instances[0].patched)["recMAIL"] == {"Phone": "+1 777"}


def test_result_with_no_updates_is_skipped_with_reason(client):
    body = _post(client, [{"linkedin_url": ADAM}])
    assert body["filled"] == 0 and body["skipped"] == 1
    assert body["skip_reasons"].get("no_updates") == 1
