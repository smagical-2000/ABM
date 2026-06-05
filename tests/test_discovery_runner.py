"""On-demand discovery runner — browserless sources, deduped, into the repo."""

from datetime import UTC, datetime

import pytest

from auto_search import discovery_runner as dr
from auto_search.models import CompanyCandidate, QualificationResult, RawSignal


class _FakeRepo:
    def __init__(self):
        self.saved = []
        self.runs = []

    def already_qualified(self, key):
        return False

    def save_candidate(self, cand):
        self.saved.append(cand)
        return cand.company_key

    def start_run(self, source):
        self.runs.append(source)
        return f"run_{source}"

    def update_run(self, rid, **counts):
        pass

    def finish_run(self, rid, status, error=None):
        pass


def _cand(name):
    return CompanyCandidate(
        company_key=f"{name}co", company_name=f"{name} Co",
        signals=[RawSignal(
            source="signalbase_leadership", source_external_id=f"{name}::1",
            signal_type="leadership_change", company_name_raw=f"{name} Co",
            observed_at=datetime(2026, 6, 5, tzinfo=UTC), signal_strength=0.9)],
        qualification=QualificationResult(
            qualified=True, confidence=0.9, reasoning="fit", segment="health_system"))


@pytest.mark.asyncio
async def test_run_once_qualifies_browserless_sources(monkeypatch):
    repo = _FakeRepo()

    async def fake_pipeline_run(connector, since, **kw):
        yield _cand("acme")                       # one qualified company per source

    monkeypatch.setattr(dr, "_connector", lambda name, limit: object())  # no real connector
    monkeypatch.setattr(dr.pipeline, "run", fake_pipeline_run)

    costs = []
    summary = await dr.run_once(repo, days=1, on_cost=lambda n: costs.append(n))

    assert summary["ran"] == 4                    # leadership, acquisitions, funding, jobs
    assert summary["qualified"] == 4
    assert len(repo.saved) == 4                   # candidates persisted to the panel repo
    assert "layoffs" not in repo.runs             # never the browser source
    assert costs == [4]                           # qualify cost recorded for 4 evaluated


@pytest.mark.asyncio
async def test_run_once_resilient_to_one_source_failing(monkeypatch):
    repo = _FakeRepo()

    async def flaky(connector, since, **kw):
        # acquisitions blows up; others yield one
        if getattr(connector, "boom", False):
            raise RuntimeError("apify down")
            yield  # pragma: no cover
        yield _cand("ok")

    def fake_connector(name, limit):
        c = object.__new__(type("C", (), {}))
        c.boom = (name == "acquisitions")
        return c

    monkeypatch.setattr(dr, "_connector", fake_connector)
    monkeypatch.setattr(dr.pipeline, "run", flaky)

    summary = await dr.run_once(repo, days=1)
    assert summary["ran"] == 3                     # 3 ok, 1 failed, run continued
    assert summary["by_source"]["acquisitions"]["error"]
    assert summary["qualified"] == 3
