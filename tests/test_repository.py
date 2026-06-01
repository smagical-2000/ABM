"""Tests for the JSON repository — idempotent upsert, signal dedup, and the
across-run skip that prevents repeat Claude calls.
"""

from datetime import UTC, datetime

from auto_search.db.repository import JsonFileRepository
from auto_search.models import CompanyCandidate, QualificationResult, RawSignal


def _signal(ext_id: str, **payload) -> RawSignal:
    return RawSignal(
        source="warntracker",
        source_external_id=ext_id,
        signal_type="layoff",
        company_name_raw="Acme Health LLC",
        observed_at=datetime(2026, 3, 1, tzinfo=UTC),
        signal_strength=0.75,
        payload={"laid_off_count": 135, "state": "OH", "city": "Toledo", **payload},
    )


def _candidate(*signals, qualified=True, needs_review=False, is_error=False):
    return CompanyCandidate(
        company_key="acmehealth",
        company_name="Acme Health LLC",
        signals=list(signals),
        qualification=QualificationResult(
            qualified=qualified, confidence=0.88, reasoning="community hospital",
            segment="health_system", needs_human_review=needs_review,
            is_error=is_error,
        ),
    )


def test_save_then_already_qualified(tmp_path):
    repo = JsonFileRepository(tmp_path / "store.json")
    assert repo.already_qualified("acmehealth") is False
    repo.save_candidate(_candidate(_signal("a::1")))
    assert repo.already_qualified("acmehealth") is True


def test_idempotent_company_and_signal_dedup(tmp_path):
    path = tmp_path / "store.json"
    cand = _candidate(_signal("a::1"), _signal("a::2"))

    JsonFileRepository(path).save_candidate(cand)
    # Re-open (fresh load) and save the same candidate again.
    JsonFileRepository(path).save_candidate(cand)

    store = JsonFileRepository(path)._store
    assert len(store) == 1                                   # company not duplicated
    assert len(store["acmehealth"]["signals"]) == 2         # signals not duplicated


def test_error_status_is_retryable(tmp_path):
    # is_error verdicts must NOT count as "already decided", so a transient
    # failure gets another attempt next run.
    repo = JsonFileRepository(tmp_path / "store.json")
    repo.save_candidate(_candidate(_signal("a::1"), is_error=True, qualified=False))
    assert repo.already_qualified("acmehealth") is False


def test_signal_summary_generated(tmp_path):
    repo = JsonFileRepository(tmp_path / "store.json")
    repo.save_candidate(_candidate(_signal("a::1")))
    summary = repo._store["acmehealth"]["signals"][0]["summary"]
    assert summary == "135 laid off in Toledo"


def test_corrupt_store_is_preserved_not_wiped(tmp_path):
    path = tmp_path / "store.json"
    path.write_text("{ this is not valid json")
    repo = JsonFileRepository(path)          # load() handles corruption
    assert repo._store == {}
    assert path.with_suffix(".json.corrupt").exists()   # forensic copy kept


def _candidate_named(key, name, *, status):
    return CompanyCandidate(
        company_key=key,
        company_name=name,
        signals=[RawSignal(
            source="signalbase_leadership", source_external_id=f"{key}::1",
            signal_type="leadership_change", company_name_raw=name,
            observed_at=datetime(2026, 5, 1, tzinfo=UTC), signal_strength=0.9,
        )],
        qualification=QualificationResult(
            qualified=(status == "qualified"),
            needs_human_review=(status == "needs_review"),
            is_error=(status == "error"),
            confidence=0.9, reasoning="x", segment="health_system",
        ),
    )


def test_panel_returns_only_qualified(tmp_path):
    repo = JsonFileRepository(tmp_path / "store.json")
    repo.save_candidate(_candidate_named("alpha", "Alpha Health", status="qualified"))
    repo.save_candidate(_candidate_named("bravo", "Bravo Clinic", status="disqualified"))
    repo.save_candidate(_candidate_named("charlie", "Charlie CNO", status="needs_review"))

    panel = repo.panel()                      # default: qualified only
    assert {r["display_name"] for r in panel} == {"Alpha Health"}

    both = repo.panel(statuses=("qualified", "needs_review"))
    assert {r["display_name"] for r in both} == {"Alpha Health", "Charlie CNO"}


def test_stats_counts_by_status(tmp_path):
    repo = JsonFileRepository(tmp_path / "store.json")
    repo.save_candidate(_candidate_named("alpha", "Alpha", status="qualified"))
    repo.save_candidate(_candidate_named("bravo", "Bravo", status="disqualified"))
    s = repo.stats()
    assert s["qualified"] == 1 and s["disqualified"] == 1 and s["total"] == 2
