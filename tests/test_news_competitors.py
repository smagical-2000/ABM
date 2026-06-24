"""Competitor distress monitor — pure query/name helpers + the store path (mocked feeds)."""

from __future__ import annotations

import pytest

from auto_search.news import competitors
from auto_search.news.models import NewsItem


def test_competitor_query_quotes_name_and_adds_negative_terms():
    q = competitors.competitor_query("R1 RCM")
    assert q.startswith('"R1 RCM" (')
    assert "layoffs" in q and "lawsuit" in q and "bankruptcy" in q


def test_competitor_names_filters_and_dedups():
    targets = [
        {"label": "R1 RCM", "kind": "competitor", "active": True},
        {"label": "R1 RCM", "kind": "competitor", "active": True},   # dup
        {"label": "Magical", "kind": "own", "active": True},          # not a competitor
        {"label": "Sleepy", "kind": "competitor", "active": False},   # inactive
        {"label": None, "kind": "competitor", "active": True},        # numeric-id, no name
        {"label": "Tennr", "kind": "competitor", "active": True},
    ]
    assert competitors.competitor_names(targets) == ["R1 RCM", "Tennr"]


def test_build_queries_topic_prefix():
    qs = competitors.build_queries(["Arintra"])
    assert list(qs) == ["Competitor: Arintra"]
    assert qs["Competitor: Arintra"].startswith('"Arintra" (')


class _FakeRepo:
    def __init__(self, names, existing=()):
        self._targets = [{"label": n, "kind": "competitor", "active": True} for n in names]
        self._existing = set(existing)
        self.saved: list[dict] = []

    def social_targets(self):
        return self._targets

    def news_urls(self):
        return list(self._existing)

    def save_news_items(self, rows):
        self.saved.extend(rows)


@pytest.mark.asyncio
async def test_run_competitor_news_stores_fresh_with_play(monkeypatch):
    async def fake_fetch(queries, *, max_per_query=8, recency="30d", timeout=20.0):
        # one hit per competitor topic + one already-seen URL (deduped out)
        out = [NewsItem(url=f"http://n/{i}", title=f"{topic} trouble", topic=topic,
                        fetched_at="2026-06-23T00:00:00Z")
               for i, topic in enumerate(queries)]
        out.append(NewsItem(url="http://seen", title="old", topic=next(iter(queries)),
                            fetched_at="2026-06-23T00:00:00Z"))
        return out

    monkeypatch.setattr(competitors.feeds, "fetch_queries", fake_fetch)
    repo = _FakeRepo(["R1 RCM", "Tennr"], existing={"http://seen"})

    summary = await competitors.run_competitor_news(repo)

    assert summary["competitors"] == 2
    assert summary["stored"] == 2                       # the "seen" URL was deduped out
    assert len(repo.saved) == 2
    row = repo.saved[0]
    assert row["topic"].startswith("Competitor: ")
    assert row["relevant"] is True and row["get_behind"] == 60
    assert "Fast-follower" in row["play"]
    assert "competitor" in row["why_it_matters"].lower()


@pytest.mark.asyncio
async def test_run_competitor_news_noop_without_competitors(monkeypatch):
    called = False

    async def fake_fetch(*a, **k):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(competitors.feeds, "fetch_queries", fake_fetch)
    summary = await competitors.run_competitor_news(_FakeRepo([]))
    assert summary == {"competitors": 0, "items": 0, "stored": 0}
    assert called is False           # no names → no fetch


def test_launch_set_has_expected_competitors():
    labels = {c.get("label") for c in competitors.COMPETITORS}
    assert {"R1 RCM", "UiPath", "AssortHealth", "Tennr"} <= labels
    # the numeric-id target is present but nameless (social-only)
    assert any(c.get("label") is None for c in competitors.COMPETITORS)
