"""Jobs signal-stacking — the per-company qualify gate, the watch ledger, and
how the pipeline + runner use them.

Layers covered:
  • pure decision (stacking_decision / should_park) — core vs standard, volume,
    non-job signals, fail-open on unknown tier
  • watch_record — the compact row persisted for a parked company
  • pipeline defer/on_defer hook — parks a single standard posting, qualifies a
    stacked one (qualify monkeypatched as the spend proxy)
  • JSON parked store — upsert, list, graduation (qualified → hidden), TTL prune
  • discovery_runner end-to-end — single standard parked, stacked qualified
"""

from datetime import UTC, datetime, timedelta

import pytest

from auto_search import job_stacking, pipeline
from auto_search.db.repository import JsonFileRepository
from auto_search.job_stacking import should_park, stacking_decision, watch_record
from auto_search.models import CompanyCandidate, QualificationResult, RawSignal

SINCE = datetime(2026, 6, 1, tzinfo=UTC)


def _job(company, role, tier, *, ext, strength=0.7, **payload):
    """A job_posting signal carrying role + tier (what the gate reads)."""
    return RawSignal(
        source="indeed", source_external_id=ext, signal_type="job_posting",
        company_name_raw=company, company_domain_raw=payload.pop("domain", None),
        observed_at=datetime(2026, 6, 5, tzinfo=UTC), signal_strength=strength,
        payload={"role": role, "tier": tier, "job_title": f"{role} role",
                 "job_url": f"https://example.com/{ext}", **payload})


def _other(company, ext):
    """A non-job signal (leadership) — must never be suppressed by the jobs gate."""
    return RawSignal(
        source="signalbase_leadership", source_external_id=ext,
        signal_type="leadership_change", company_name_raw=company,
        observed_at=datetime(2026, 6, 5, tzinfo=UTC), signal_strength=0.9)


# ── pure decision ──────────────────────────────────────────────────────


class TestStackingDecision:
    def test_single_standard_is_parked(self):
        d = stacking_decision([_job("Acme", "Coder", "standard", ext="a1")])
        assert d.action == "park" and d.parked
        assert d.standard_postings == 1 and d.standard_roles == ("Coder",)
        assert "Coder" in d.reason
        assert should_park([_job("Acme", "Coder", "standard", ext="a1")])

    def test_single_core_qualifies(self):
        d = stacking_decision([_job("Acme", "Denials", "core", ext="a1")])
        assert d.action == "qualify" and not d.parked
        assert d.core_roles == ("Denials",)
        assert not should_park([_job("Acme", "Denials", "core", ext="a1")])

    def test_two_distinct_standard_roles_stack(self):
        d = stacking_decision([_job("Acme", "Coder", "standard", ext="a1"),
                               _job("Acme", "Biller", "standard", ext="a2")])
        assert d.action == "qualify"
        assert d.standard_postings == 2 and set(d.standard_roles) == {"Coder", "Biller"}

    def test_two_same_standard_postings_stack_on_volume(self):
        # Volume counts: two open billers is a build-out even if it's one role.
        d = stacking_decision([_job("Acme", "Biller", "standard", ext="a1"),
                               _job("Acme", "Biller", "standard", ext="a2")])
        assert d.action == "qualify"
        assert d.standard_postings == 2 and d.standard_roles == ("Biller",)

    def test_core_plus_standard_qualifies_on_core(self):
        d = stacking_decision([_job("Acme", "Denials", "core", ext="a1"),
                               _job("Acme", "Coder", "standard", ext="a2")])
        assert d.action == "qualify" and d.core_roles == ("Denials",)

    def test_non_job_signal_never_parks(self):
        # A lone standard posting + a leadership signal → qualify (don't suppress).
        d = stacking_decision([_job("Acme", "Coder", "standard", ext="a1"),
                               _other("Acme", "l1")])
        assert d.action == "qualify" and "non-job" in d.reason

    def test_unknown_tier_fails_open(self):
        sig = RawSignal(
            source="indeed", source_external_id="a1", signal_type="job_posting",
            company_name_raw="Acme", observed_at=SINCE, signal_strength=0.7,
            payload={"role": "Mystery"})            # no tier key at all
        assert stacking_decision([sig]).action == "qualify"


def test_watch_record_shape():
    sigs = [_job("Acme Health", "Coder", "standard", ext="a1", domain="acme.org",
                 state="TX", city="Dallas", job_title="Medical Coder")]
    r = watch_record("acmehealth", sigs, stacking_decision(sigs))
    assert r["company_key"] == "acmehealth"
    assert r["name"] == "Acme Health" and r["domain"] == "acme.org"
    assert r["role"] == "Coder" and r["postings"] == 1
    assert r["sample_url"] == "https://example.com/a1"
    assert r["sample_title"] == "Medical Coder"
    assert r["state"] == "TX" and r["city"] == "Dallas"


# ── pipeline defer/on_defer hook ───────────────────────────────────────


class _JobsConnector:
    def __init__(self, signals):
        self._signals = signals

    async def pull(self, *, since):
        for s in self._signals:
            yield s


def _patch_qualify(monkeypatch):
    """Replace the paid qualifier with a counter; returns the call log."""
    calls = []

    async def fake_qualify(signal):
        calls.append(signal.company_name_raw)
        return QualificationResult(qualified=True, confidence=0.9,
                                   reasoning="fit", segment="health_system")

    monkeypatch.setattr(pipeline, "qualify", fake_qualify)
    return calls


@pytest.mark.asyncio
async def test_pipeline_defers_single_standard_qualifies_stacked(monkeypatch):
    calls = _patch_qualify(monkeypatch)
    sigs = [_job("Solo Health", "Coder", "standard", ext="s1"),
            _job("Stack Health", "Coder", "standard", ext="t1"),
            _job("Stack Health", "Biller", "standard", ext="t2"),
            _job("Core Health", "Denials", "core", ext="c1")]
    parked = []

    out = [c async for c in pipeline.run(
        _JobsConnector(sigs), SINCE,
        defer=lambda k, s: should_park(s),
        on_defer=lambda k, s: parked.append(k))]

    assert {c.company_name for c in out} == {"Stack Health", "Core Health"}
    assert "Solo Health" not in calls                 # no paid qualify for parked
    assert parked == [sigs[0].company_key]            # solo parked exactly once


# ── JSON parked store ──────────────────────────────────────────────────


def _qualified_cand(key, name):
    return CompanyCandidate(
        company_key=key, company_name=name,
        signals=[_job(name, "Coder", "standard", ext=f"{key}1")],
        qualification=QualificationResult(
            qualified=True, confidence=0.9, reasoning="fit", segment="health_system"))


class TestParkedStore:
    def _repo(self, tmp_path):
        return JsonFileRepository(path=tmp_path / "store.json")

    def test_upsert_and_list(self, tmp_path):
        repo = self._repo(tmp_path)
        repo.upsert_parked({"company_key": "acme", "name": "Acme", "role": "Coder",
                            "postings": 1})
        rows = repo.parked_companies()
        assert len(rows) == 1 and rows[0]["company_key"] == "acme"
        assert rows[0]["first_parked_at"] and rows[0]["last_seen_at"]

    def test_upsert_updates_fields_keeps_first_parked(self, tmp_path):
        repo = self._repo(tmp_path)
        first = repo.upsert_parked({"company_key": "acme", "name": "Acme"})["first_parked_at"]
        repo.upsert_parked({"company_key": "acme", "name": "Acme 2", "postings": 3})
        row = repo.parked_companies()[0]
        assert row["first_parked_at"] == first       # insert stamp preserved
        assert row["name"] == "Acme 2" and row["postings"] == 3

    def test_graduated_company_hidden_from_watch(self, tmp_path):
        repo = self._repo(tmp_path)
        repo.upsert_parked({"company_key": "acme", "name": "Acme"})
        repo.save_candidate(_qualified_cand("acme", "Acme"))   # it later qualifies
        assert repo.parked_companies() == []                   # excluded once decided

    def test_stale_entries_pruned(self, tmp_path):
        repo = self._repo(tmp_path)
        repo.upsert_parked({"company_key": "old", "name": "Old"})
        rows = repo._parked()
        rows[0]["last_seen_at"] = (
            datetime.now(UTC) - timedelta(days=job_stacking.PARK_TTL_DAYS + 1)).isoformat()
        repo._write_parked(rows)
        assert repo.parked_companies() == []                   # past TTL → dropped


# ── discovery_runner end-to-end ────────────────────────────────────────


class _ParkRepo:
    def __init__(self):
        self.saved = []
        self.parked = []

    def already_qualified(self, key):
        return False

    def save_candidate(self, cand):
        self.saved.append(cand)
        return cand.company_key

    def upsert_parked(self, record):
        self.parked.append(record)
        return record

    def start_run(self, source):
        return 1

    def update_run(self, *a, **k):
        pass

    def finish_run(self, *a, **k):
        pass


@pytest.mark.asyncio
async def test_runner_parks_single_standard_qualifies_stacked(monkeypatch):
    from auto_search import discovery_runner as dr

    calls = _patch_qualify(monkeypatch)

    async def passthrough(sigs, **kw):            # skip the paid job prefilter
        return sigs

    monkeypatch.setattr(dr.job_qualifier, "filter_job_signals", passthrough)

    sigs = [_job("Solo Health", "Coder", "standard", ext="s1"),
            _job("Stack Health", "Coder", "standard", ext="t1"),
            _job("Stack Health", "Biller", "standard", ext="t2")]
    monkeypatch.setattr(dr, "_connector", lambda name, limit: _JobsConnector(sigs))

    repo = _ParkRepo()
    summary = await dr.run_once(repo, days=1, sources=["jobs"])

    assert summary["qualified"] == 1                      # Stack Health
    assert summary["parked"] == 1                         # Solo Health
    assert [c.company_name for c in repo.saved] == ["Stack Health"]
    assert calls == ["Stack Health"]                      # only one paid qualify
    assert len(repo.parked) == 1 and repo.parked[0]["name"] == "Solo Health"
