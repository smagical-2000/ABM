"""News: Google-News RSS parsing + the runner (fetch -> new-only -> enrich -> store)."""

import pytest

from auto_search.news import enrich as enrich_mod
from auto_search.news import feeds, runner
from auto_search.news.models import NewsItem

_RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item>
  <title>CMS finalizes electronic prior authorization rule - Becker's Hospital Review</title>
  <link>https://news.google.com/rss/articles/x1</link>
  <pubDate>Mon, 09 Jun 2026 12:00:00 GMT</pubDate>
  <description>&lt;a href="x"&gt;CMS finalizes electronic prior authorization rule&lt;/a&gt;</description>
  <source url="https://beckershospitalreview.com">Becker's Hospital Review</source>
</item>
<item>
  <title>Some clinical headline - Healthcare Dive</title>
  <link>https://news.google.com/rss/articles/x2</link>
  <pubDate>Sun, 08 Jun 2026 09:00:00 GMT</pubDate>
</item>
</channel></rss>"""


def test_parse_rss_strips_source_suffix_and_dates():
    items = feeds._parse(_RSS, "prior_auth", "2026-06-09T00:00:00+00:00")
    assert len(items) == 2
    a = items[0]
    assert a.title == "CMS finalizes electronic prior authorization rule"  # " - Source" dropped
    assert a.source == "Becker's Hospital Review"
    assert a.url == "https://news.google.com/rss/articles/x1"
    assert a.topic == "prior_auth"
    assert a.published_at and a.published_at.startswith("2026-06-09")


class _FakeRepo:
    def __init__(self, existing=()):
        self._existing = list(existing)
        self.saved = []

    def news_urls(self):
        return list(self._existing)

    def save_news_items(self, items):
        self.saved.extend(items)
        return len(items)


@pytest.mark.asyncio
async def test_runner_enriches_only_new_and_drops_irrelevant(monkeypatch):
    fetched = [
        NewsItem(url="u1", title="Prior auth rule", topic="prior_auth"),
        NewsItem(url="u2", title="Irrelevant", topic="policy"),
        NewsItem(url="u_old", title="Already stored", topic="denials"),
    ]

    async def fake_fetch(**_kw):
        return fetched

    async def fake_enrich(items):
        for it in items:
            it.relevant = it.url != "u2"             # the model drops u2
            it.why_it_matters = "angle" if it.relevant else None
        return 0.02

    monkeypatch.setattr(feeds, "fetch_all", fake_fetch)
    monkeypatch.setattr(enrich_mod, "enrich", fake_enrich)

    repo = _FakeRepo(existing=["u_old"])             # u_old already known -> not re-enriched
    costs = []
    summary = await runner.run_once(repo, on_cost=costs.append)

    assert summary["fetched"] == 3
    assert summary["new"] == 2                        # u1, u2 (u_old skipped)
    assert summary["dropped_irrelevant"] == 1         # u2 dropped by enrich
    assert summary["stored"] == 1                     # only u1 stored
    assert [it["url"] for it in repo.saved] == ["u1"]
    assert costs == [0.02]


@pytest.mark.asyncio
async def test_enrich_parses_get_behind_and_play(monkeypatch):
    import json as _json
    items = [NewsItem(url="u1", title="CMS forces 72-hour prior auth decisions")]
    verdict = [{"id": "0", "relevant": True, "topic": "prior_auth",
                "why_it_matters": "manual auth can't keep up",
                "get_behind": 92, "play": "target systems hiring prior-auth staff"}]

    async def fake_call(**_kw):
        return object()

    monkeypatch.setattr(enrich_mod.llm, "call_plain", fake_call)
    monkeypatch.setattr(enrich_mod.llm, "extract_text", lambda _r: _json.dumps(verdict))
    monkeypatch.setattr(enrich_mod.llm, "spend_from_response", lambda _r, model=None: None)

    await enrich_mod.enrich(items)
    assert items[0].get_behind == 92
    assert items[0].play == "target systems hiring prior-auth staff"
    assert items[0].topic == "prior_auth" and items[0].why_it_matters


def test_json_repo_news_ranked_by_get_behind(tmp_path):
    from auto_search.db.repository import JsonFileRepository
    repo = JsonFileRepository(path=str(tmp_path / "disco.json"))
    repo.save_news_items([
        {"url": "a", "title": "low", "get_behind": 30, "relevant": True, "published_at": "2026-06-10"},
        {"url": "b", "title": "high", "get_behind": 90, "relevant": True, "published_at": "2026-06-01"},
    ])
    # higher get-behind wins over recency
    assert [r["url"] for r in repo.news_items()] == ["b", "a"]


@pytest.mark.asyncio
async def test_reenrich_stored_backfills_new_fields(monkeypatch, tmp_path):
    from auto_search.db.repository import JsonFileRepository
    repo = JsonFileRepository(path=str(tmp_path / "d.json"))
    repo.save_news_items([{"url": "u1", "title": "CMS prior auth rule",
                           "topic": "prior_auth", "relevant": True}])

    async def fake_enrich(items):
        for it in items:
            it.get_behind = 88
            it.play = "target prior-auth-heavy systems"
        return 0.01

    monkeypatch.setattr(enrich_mod, "enrich", fake_enrich)
    summary = await runner.reenrich_stored(repo)
    assert summary["reenriched"] == 1
    back = repo.news_items()[0]
    assert back["get_behind"] == 88 and back["play"] == "target prior-auth-heavy systems"
