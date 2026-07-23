"""MAR2-32 trust monitor: the invariants catch what incident-QA cannot —
false negatives leave no artifact to pin, so the system re-proves itself from
raw events instead. Red holds the notifier (tested via the violations here;
the endpoint returns held=True whenever ok is False)."""
from __future__ import annotations

import pytest

from auto_search.db.engagement_repository import EngagementJsonRepository
from auto_search.engagement import audit


class _Scoring:
    def __init__(self, rows):
        self._rows = rows

    def list_accounts(self):
        return self._rows


class _Discovery:
    def __init__(self, targets):
        self._t = targets

    def abm_targets(self):
        return self._t


@pytest.fixture
def repo(tmp_path):
    return EngagementJsonRepository(tmp_path / "store.json")


def _ev(repo, aid, ext, kind, pts, when="2026-07-01T00:00:00+00:00",
        company="Summa Health System"):
    repo.add_event({"source": "sfdc", "external_id": ext, "channel": "crm",
                    "kind": kind, "points": pts, "contact_ext": ext,
                    "company": company, "account_id": aid,
                    "campaign": None, "occurred_at": when, "raw": {}})


def _healed(repo):
    """Stamp the heal marker as the ingest pipeline does — I5 requires every
    ingest to be followed by a self-heal."""
    import json
    from datetime import UTC, datetime
    repo.set_setting("identity_heal_last", json.dumps(
        {"at": datetime.now(UTC).isoformat(), "merged": 0, "manual": 0}))


def test_clean_store_passes(repo):
    scoring_repo = _Scoring([{"account_id": "csv_acme_health", "name": "Acme Health",
                              "domain": "acme.com"}])
    _ev(repo, "csv_acme_health", "crm:meeting:1", "meeting_booked", 10,
        company="Acme Health")
    _healed(repo)
    rep = audit.run_invariants(repo, scoring_repo, _Discovery([]))
    assert rep["ok"] and rep["violations"] == []
    assert rep["stats"]["tiles"] == 1


def test_ingest_without_heal_trips_I5(repo):
    """The 2026-07-14 incident: a stale discovery-cron container (built before
    the heal existed) wrote rows to the shared store — no marker follows the
    ingest, so the board must go red instead of silently splitting."""
    scoring_repo = _Scoring([{"account_id": "csv_acme_health", "name": "Acme Health"}])
    _ev(repo, "csv_acme_health", "crm:meeting:1", "meeting_booked", 10,
        company="Acme Health")
    rep = audit.run_invariants(repo, scoring_repo, _Discovery([]))
    assert not rep["ok"]
    assert any(v["code"] == "I5-stale-heal" for v in rep["violations"])


def test_real_heal_writes_marker_and_clears_I5(repo):
    """End-to-end: ingest (red) → heal (marker) → green."""
    from auto_search.engagement import identity
    scoring_repo = _Scoring([{"account_id": "csv_acme_health", "name": "Acme Health"}])
    _ev(repo, "csv_acme_health", "crm:meeting:1", "meeting_booked", 10,
        company="Acme Health")
    assert not audit.run_invariants(repo, scoring_repo, _Discovery([]))["ok"]
    identity.heal_identity_splits(repo, scoring_repo, _Discovery([]))
    rep = audit.run_invariants(repo, scoring_repo, _Discovery([]))
    assert rep["ok"] is True


def test_twin_split_trips_I1_and_I4(repo):
    """THE incident shape: company-level Hot (31) split Warm+Warm across twin
    tiles, already notified at Warm — no tile can ever fire the Hot alert.
    I1 sees the unhealed split; I4 sees the silent company-level due."""
    scoring_repo = _Scoring([{"account_id": "csv_summa_health_system",
                              "name": "Summa Health System", "domain": "summa.org"}])
    discovery = _Discovery([{"name": "Summa Health System", "domain": "summa.org"}])
    # csv tile: 10 + 6 + 4 = 20 (Warm) · abm tile: 10 + 1 = 11 (Some) → merged 31 Hot
    _ev(repo, "csv_summa_health_system", "crm:meeting:1", "meeting_booked", 10)
    _ev(repo, "csv_summa_health_system", "email:reply:2", "reply", 6)
    _ev(repo, "csv_summa_health_system", "pod:3", "podcast_lead", 4)
    _ev(repo, "abm_summahealthsystem", "crm:meeting:4", "meeting_booked", 10)
    _ev(repo, "abm_summahealthsystem", "email:click:5", "click", 1)
    # I4 models the ABM-only sender (2026-07-23): membership comes off the
    # contacts' matched_lists — the scored twin alone must not read non-ABM.
    repo.upsert_contact({"source": "sfdc", "external_id": "ct1",
                         "company": "Summa Health System",
                         "account_id": "csv_summa_health_system",
                         "matched_lists": ["scored"]})
    repo.upsert_contact({"source": "sfdc", "external_id": "ct2",
                         "company": "Summa Health System",
                         "account_id": "abm_summahealthsystem",
                         "matched_lists": ["abm"]})
    repo.set_setting("notified_tiers", '{"summahealthsystem": {"tier": "Warm", '
                     '"touch": "2026-06-30T00:00:00+00:00"}}')

    rep = audit.run_invariants(repo, scoring_repo, discovery)
    codes = {v["code"] for v in rep["violations"]}
    assert not rep["ok"]
    assert "I1-twins" in codes
    assert "I4-diverge" in codes


def test_points_drift_trips_I2(repo):
    """A stored event whose points no longer match the canonical matrix
    (the linkedin_tofu 2-vs-6 stragglers found on prod 2026-07-13)."""
    scoring_repo = _Scoring([{"account_id": "csv_acme_health", "name": "Acme Health"}])
    _ev(repo, "csv_acme_health", "li:1", "linkedin_tofu", 2, company="Acme Health")
    rep = audit.run_invariants(repo, scoring_repo, _Discovery([]))
    assert not rep["ok"]
    assert any(v["code"] == "I2-points" for v in rep["violations"])


def test_domain_conflict_is_manual_review_not_violation(repo):
    """Healthfirst class: a red light nobody can clear trains people to ignore
    red lights — domain conflicts surface as manual_review, board stays ok."""
    scoring_repo = _Scoring([{"account_id": "acc_healthfirst", "name": "Healthfirst",
                              "domain": "hf.org"}])
    discovery = _Discovery([{"name": "Healthfirst", "domain": "healthfirst.org"}])
    _ev(repo, "acc_healthfirst", "email:click:1", "click", 1, company="Healthfirst")
    _ev(repo, "abm_healthfirst", "email:click:2", "click", 1, company="Healthfirst")
    _healed(repo)
    rep = audit.run_invariants(repo, scoring_repo, discovery)
    assert rep["ok"] is True
    assert len(rep["manual_review"]) == 1
