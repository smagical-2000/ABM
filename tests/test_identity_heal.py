"""MAR2-32: identity self-heal — one company, one account id.

The incident replayed: bulk imports minted csv_* twins for companies whose
history lived on abm_* ids; tiers computed per tile hid company-level Hots
(Summa 31 read as 19+12; CORA's genuine Hot never fired). These tests pin the
heal that makes the split class impossible, and its safety guards."""
from __future__ import annotations

import pytest

from auto_search.db.engagement_repository import EngagementJsonRepository
from auto_search.engagement import identity


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


def _ev(repo, aid, ext, kind="click", pts=1, when="2026-07-01T00:00:00+00:00"):
    repo.add_event({"source": "replyio", "external_id": ext, "channel": "email",
                    "kind": kind, "points": pts, "contact_ext": ext,
                    "company": "Acme Health", "account_id": aid,
                    "campaign": None, "occurred_at": when, "raw": {}})


def test_heal_merges_abm_twin_into_scored_id(repo):
    scoring_repo = _Scoring([{"account_id": "csv_acme_health", "name": "Acme Health",
                              "domain": "acme.com"}])
    discovery = _Discovery([{"name": "Acme Health", "domain": "acme.com"}])
    _ev(repo, "abm_acmehealth", "email:click:1")
    _ev(repo, "csv_acme_health", "email:click:2")
    repo.claim_activation("abm_acmehealth")

    rep = identity.heal_identity_splits(repo, scoring_repo, discovery)
    assert rep["merged"] == {"abm_acmehealth": "csv_acme_health"}
    assert rep["events"] == 1 and rep["activations"] == 1
    assert {r["account_id"] for r in repo.engaged_accounts()} == {"csv_acme_health"}
    # the company's combined heat now lives on ONE tile
    assert repo.engaged_accounts()[0]["score"] == 2
    # activation followed the company (a rep's claim survives the re-key)
    assert repo.is_activated("csv_acme_health")
    assert not repo.is_activated("abm_acmehealth")
    # idempotent: a second heal finds nothing to do
    assert identity.heal_identity_splits(repo, scoring_repo, discovery)["merged"] == {}


def test_conflicting_domains_go_to_manual_never_auto(repo):
    """The Healthfirst class: same name key, DIFFERENT domains — could be two
    real companies. Must never auto-merge; surfaced for a human instead."""
    scoring_repo = _Scoring([{"account_id": "acc_healthfirst", "name": "Healthfirst",
                              "domain": "hf.org"}])
    discovery = _Discovery([{"name": "Healthfirst", "domain": "healthfirst.org"}])
    _ev(repo, "acc_healthfirst", "email:click:1")
    _ev(repo, "abm_healthfirst", "email:click:2")

    rep = identity.heal_identity_splits(repo, scoring_repo, discovery)
    assert rep["merged"] == {}
    assert len(rep["manual"]) == 1 and "conflicting domains" in rep["manual"][0]["why"]
    assert {r["account_id"] for r in repo.engaged_accounts()} == {
        "acc_healthfirst", "abm_healthfirst"}   # untouched


def test_two_canonical_candidates_never_guess(repo):
    """csv_ AND acc_ ids for one company: ambiguous — manual, never a guess."""
    scoring_repo = _Scoring([
        {"account_id": "csv_acme_health", "name": "Acme Health", "domain": "acme.com"},
        {"account_id": "acc_acmehealth", "name": "Acme Health", "domain": "acme.com"},
    ])
    discovery = _Discovery([])
    _ev(repo, "csv_acme_health", "email:click:1")
    _ev(repo, "acc_acmehealth", "email:click:2")

    rep = identity.heal_identity_splits(repo, scoring_repo, discovery)
    assert rep["merged"] == {}
    assert rep["manual"][0]["why"] == "no single canonical id"


def test_dry_run_moves_nothing(repo):
    scoring_repo = _Scoring([{"account_id": "csv_acme_health", "name": "Acme Health"}])
    discovery = _Discovery([{"name": "Acme Health"}])
    _ev(repo, "abm_acmehealth", "email:click:1")
    _ev(repo, "csv_acme_health", "email:click:2")

    rep = identity.heal_identity_splits(repo, scoring_repo, discovery, dry_run=True)
    assert rep["merged"] == {"abm_acmehealth": "csv_acme_health"}
    assert {r["account_id"] for r in repo.engaged_accounts()} == {
        "csv_acme_health", "abm_acmehealth"}   # still split — dry run


def test_minimal_fake_repo_is_skipped():
    """cross_and_persist runs the heal after every ingest; test fakes without
    the rekey surface must pass through untouched (capability gate)."""

    class Bare:
        pass

    rep = identity.heal_identity_splits(Bare(), _Scoring([]), _Discovery([]))
    assert rep.get("skipped") and rep["merged"] == {}
