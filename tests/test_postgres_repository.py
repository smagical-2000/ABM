"""PostgresRepository tests — run only when a Postgres is reachable.

Skipped automatically in CI (no DATABASE_URL / no server), so the suite stays
green everywhere; run locally with Postgres up to exercise the SQL path.

Uses a unique key per run and cleans up, so it can share the dev database
without polluting it.
"""

import os
import uuid
from datetime import UTC, datetime

import pytest
from dotenv import load_dotenv

from auto_search.models import CompanyCandidate, QualificationResult, RawSignal

load_dotenv(override=True)
DATABASE_URL = os.getenv("DATABASE_URL")


def _pg_reachable() -> bool:
    if not DATABASE_URL:
        return False
    try:
        import psycopg

        with psycopg.connect(DATABASE_URL, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_reachable(), reason="no reachable Postgres (DATABASE_URL)"
)


@pytest.fixture
def repo():
    from auto_search.db.postgres_repository import PostgresRepository

    r = PostgresRepository(DATABASE_URL)
    yield r
    r.close()


def _cand(key, name, *, status="qualified"):
    return CompanyCandidate(
        company_key=key,
        company_name=name,
        signals=[RawSignal(
            source="signalbase_leadership", source_external_id=f"{key}::1",
            signal_type="leadership_change", company_name_raw=name,
            observed_at=datetime(2026, 5, 1, tzinfo=UTC), signal_strength=0.9,
            payload={"new_role": "Chief Financial Officer"},
        )],
        qualification=QualificationResult(
            qualified=(status == "qualified"),
            needs_human_review=(status == "needs_review"),
            confidence=0.88, reasoning="community hospital",
            segment="health_system", sub_segment="community_hospital",
            company_type="provider", approximate_employees=1200,
            evidence_url="https://x.org/about", domain="x.org",
        ),
    )


def test_round_trip(repo):
    key = f"pgtest{uuid.uuid4().hex[:10]}"
    try:
        repo.save_candidate(_cand(key, "PG Test Health"))
        repo.save_candidate(_cand(key, "PG Test Health"))  # idempotent

        assert repo.already_qualified(key) is True

        row = repo.get(key)
        assert row["display_name"] == "PG Test Health"
        assert row["signals"][0]["summary"] == "Chief Financial Officer"
        assert len(row["signals"]) == 1            # signal dedup

        panel = repo.panel(statuses=("qualified",))
        assert any(r["normalized_name"] == key for r in panel)

        repo.set_review(key, "promoted")
        assert repo.get(key)["review_status"] == "promoted"
    finally:
        import psycopg

        with psycopg.connect(DATABASE_URL) as c:
            c.execute(
                "DELETE FROM discovery_companies WHERE normalized_name = %s", (key,)
            )


def test_set_review_rejects_bad_status(repo):
    with pytest.raises(ValueError):
        repo.set_review("whatever", "bogus")
