"""MAR2-31: company-keyed notify ledger, cutoff gate, and ledger migration.

The 2026-07-09 incident replayed as tests: bulk imports minted new account ids
(csv_/acc_) for companies already tracked under abm_ ids; the account-id-keyed
ledger read 83 handled companies as fresh tier rises, and 49 newly-imported
companies surfaced pre-cutoff history as instant-Hot. These tests pin the three
guarantees that make that class of incident structurally impossible.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from migrate_notify_ledger import build_name_index, migrate  # noqa: E402

from auto_search.engagement import notify

# ── company_key: matching identity, never minting ────────────────────────


def test_company_key_same_company_variants_merge():
    assert notify.company_key("Ivy Rehab Network") == notify.company_key("Ivy  Rehab Network!")
    assert notify.company_key("Acme Health, LLC") == notify.company_key("Acme Health Inc.")
    # the article strip: the exact 2026-07-09 artifact class
    assert (notify.company_key("The Harris Center for Mental Health and IDD")
            == notify.company_key("Harris Center for Mental Health and IDD"))
    assert (notify.company_key("The Centers for Advanced Orthopaedics")
            == notify.company_key("Centers for Advanced Orthopaedics"))


def test_company_key_degeneracy_guard_urology_case():
    """Suffix-stripping collapses generic names hard; the article must NOT be
    stripped when a single word would remain — 'The Urology Group' and
    'Urology Associates' are DIFFERENT companies (live-data sweep, 2026-07-09)."""
    # suffix restore: single-substantive-word names keep their suffix so
    # DISTINCT companies stay distinct...
    assert notify.company_key("Urology Associates") == "urologyassociates"
    assert notify.company_key("Urology Group") == "urologygroup"
    assert notify.company_key("Urology Group") != notify.company_key("Urology Associates")
    # ...while article variants of the SAME company still merge
    assert notify.company_key("The Urology Group") == "urologygroup"


def test_company_key_empty_and_fallback():
    assert notify.company_key(None) == ""
    assert notify.company_key("") == ""
    assert notify.ledger_key({"name": "", "account_id": "abm_x"}) == "abm_x"
    assert notify.ledger_key({"name": "Acme Health", "account_id": "abm_x"}) == "acmehealth"


# ── _ledger_lookup: strongest state across every key form ────────────────


def _acct(aid, name, tier="Hot", touch="2026-07-01T00:00:00+00:00"):
    return {"account_id": aid, "name": name, "tier": tier, "last_touch": touch}


def test_ledger_lookup_company_key_form():
    led = {"ivyrehabnetwork": {"tier": "Hot", "touch": "2026-07-01T00:00:00+00:00"}}
    tier, touch = notify._ledger_lookup(led, _acct("csv_ivy_rehab_network", "Ivy Rehab Network"))
    assert tier == "Hot" and touch == "2026-07-01T00:00:00+00:00"


def test_ledger_lookup_account_id_fallback_pre_migration():
    led = {"abm_ivyrehabnetwork": {"tier": "Warm", "touch": None}}
    # the OLD id still resolves for its own row...
    tier, _ = notify._ledger_lookup(led, _acct("abm_ivyrehabnetwork", "Ivy Rehab Network"))
    assert tier == "Warm"
    # ...and the abm id BODY (canonical key) resolves for the csv twin, so a
    # re-keyed board row still sees its company's history even pre-migration.
    tier2, _ = notify._ledger_lookup(led, _acct("csv_ivy_rehab_network", "Ivy Rehab Network"))
    assert tier2 == "Warm"


def test_ledger_lookup_strongest_state_wins():
    led = {
        "ivyrehabnetwork": {"tier": "Warm", "touch": "2026-06-01T00:00:00+00:00"},
        "abm_ivyrehabnetwork": {"tier": "Hot", "touch": "2026-05-01T00:00:00+00:00"},
    }
    tier, touch = notify._ledger_lookup(led, _acct("abm_ivyrehabnetwork", "Ivy Rehab Network"))
    assert tier == "Hot"                       # highest tier wins
    assert touch == "2026-05-01T00:00:00+00:00"


def test_ledger_lookup_legacy_bare_string():
    led = {"acmehealth": "Warm"}
    tier, touch = notify._ledger_lookup(led, _acct("csv_acme_health", "Acme Health"))
    assert tier == "Warm" and touch is None


def test_ledger_lookup_unknown_company():
    tier, touch = notify._ledger_lookup({}, _acct("csv_new_co", "Brand New Co"))
    assert tier == "Lower" and touch is None


# ── accounts_to_notify: the three gates ──────────────────────────────────


def test_rekeyed_identity_does_not_refire():
    """THE incident: csv_ twin of an already-notified abm_ company must not
    fire, whether the ledger is migrated (company key) or not (abm id)."""
    # touch is POST-cutoff on purpose (QA panel: with a pre-cutoff touch this
    # test passed via the cutoff gate and never exercised the ledger at all)
    board = [_acct("csv_newport_healthcare", "Newport Healthcare", tier="Hot",
                   touch="2026-07-01T00:00:00+00:00")]
    # ledger touch NEWER than the board touch: nothing new happened — the only
    # variable is the identity re-key, which must not fire on its own. (A board
    # touch newer than the ledger touch legitimately fires hot_activity.)
    for led in ({"newporthealthcare": {"tier": "Hot", "touch": "2026-07-02T00:00:00+00:00"}},
                {"abm_newporthealthcare": {"tier": "Hot", "touch": "2026-07-02T00:00:00+00:00"}}):
        due = notify.accounts_to_notify(board, led, cutoff="2026-06-25")
        assert due == []


def test_stale_history_never_fires_cutoff():
    """Newly imported company, all touches pre-cutoff -> silent, even though it
    has no ledger entry at all (the 49-account class)."""
    board = [_acct("csv_healthcare_legal", "Healthcare Legal Solutions",
                   tier="Hot", touch="2026-04-09T00:00:00+00:00")]
    assert notify.accounts_to_notify(board, {}, cutoff="2026-06-25") == []
    # and with no touch at all
    board[0]["last_touch"] = None
    assert notify.accounts_to_notify(board, {}, cutoff="2026-06-25") == []


def test_genuine_fresh_rise_still_fires():
    """The UMMS case: fresh post-cutoff touch, never notified anywhere -> fires."""
    board = [_acct("csv_umms", "University of Maryland Medical System",
                   tier="Some", touch="2026-07-09T17:01:00+00:00")]
    due = notify.accounts_to_notify(board, {}, cutoff="2026-06-25")
    assert len(due) == 1 and due[0]["reason"] == "rose" and due[0]["role"] == "sdr"


def test_hot_reactivation_still_fires_on_newer_touch():
    led = {"acmehealth": {"tier": "Hot", "touch": "2026-07-01T00:00:00+00:00"}}
    board = [_acct("abm_acmehealth", "Acme Health", tier="Hot",
                   touch="2026-07-09T00:00:00+00:00")]
    due = notify.accounts_to_notify(board, led, cutoff="2026-06-25")
    assert len(due) == 1 and due[0]["reason"] == "hot_activity"
    # same touch -> silent
    board[0]["last_touch"] = "2026-07-01T00:00:00+00:00"
    assert notify.accounts_to_notify(board, led, cutoff="2026-06-25") == []


def test_no_cutoff_means_no_cutoff_gate():
    """cutoff=None (setting unset) preserves the old behavior exactly."""
    board = [_acct("csv_old_co", "Old Co", tier="Warm", touch="2026-01-01T00:00:00+00:00")]
    assert len(notify.accounts_to_notify(board, {}, cutoff=None)) == 1


# ── migration: names-based, collision-safe, idempotent ───────────────────


class _FakeScoring:
    def __init__(self, rows):
        self._rows = rows

    def list_accounts(self):
        return self._rows


class _FakeDiscovery:
    def __init__(self, targets):
        self._t = targets

    def abm_targets(self):
        return self._t


def _index():
    return build_name_index(
        _FakeScoring([{"account_id": "csv_ivy_rehab_network", "name": "Ivy Rehab Network"},
                      {"account_id": "acc_humana", "name": "Humana"}]),
        _FakeDiscovery([{"name": "Ivy Rehab Network"}, {"name": "Newport Healthcare"}]))


def test_migration_maps_ids_to_company_keys():
    names = _index()
    ledger = {
        "abm_ivyrehabnetwork": {"tier": "Hot", "touch": "2026-06-25T00:00:00+00:00"},
        "acc_humana": {"tier": "Hot", "touch": "2026-06-25T17:30:00+00:00"},
        "abm_newporthealthcare": "Warm",                    # legacy bare string
    }
    new, report = migrate(ledger, names)
    assert set(new) == {"ivyrehabnetwork", "humana", "newporthealthcare"}
    assert new["newporthealthcare"]["tier"] == "Warm"       # legacy form upgraded
    assert new["ivyrehabnetwork"]["account_id"] == "abm_ivyrehabnetwork"  # audit trail
    assert report["migrated"] == 3 and report["kept_unresolved"] == 0


def test_migration_collision_keeps_strongest():
    names = _index()
    ledger = {
        "abm_ivyrehabnetwork": {"tier": "Warm", "touch": "2026-06-01T00:00:00+00:00"},
        "csv_ivy_rehab_network": {"tier": "Hot", "touch": "2026-05-01T00:00:00+00:00"},
    }
    new, report = migrate(ledger, names)
    assert new["ivyrehabnetwork"]["tier"] == "Hot"          # strongest state kept
    assert len(report["collisions"]) == 1


def test_migration_unresolvable_kept_verbatim():
    ledger = {"abm_totally_unknown_co": {"tier": "Hot", "touch": None}}
    new, report = migrate(ledger, {})
    assert new == {"abm_totally_unknown_co": {"tier": "Hot", "touch": None,
                                              "account_id": "abm_totally_unknown_co"}}
    assert report["kept_unresolved"] == 1


def test_migration_idempotent():
    names = _index()
    ledger = {"abm_ivyrehabnetwork": {"tier": "Hot", "touch": "2026-06-25T00:00:00+00:00"}}
    once, _ = migrate(ledger, names)
    twice, report2 = migrate(once, names)
    assert twice == once
    assert report2["already_company_key"] == len(once)


# ── prod-shape replay: the whole incident, end to end ────────────────────


def test_incident_replay_due_collapses_to_genuine_only():
    """Models the actual 2026-07-09 production shapes: an abm/csv twin pair
    (re-key artifact), a stale-history import, a date-flip meeting (pre-cutoff
    booking re-dated), and one genuinely fresh engagement. With the migrated
    ledger + cutoff, due must contain exactly the genuine one."""
    ledger = {
        # migrated company-keyed entries (post-migration state)
        "ivyrehabnetwork": {"tier": "Hot", "touch": "2026-07-03T00:00:00+00:00",
                            "account_id": "abm_ivyrehabnetwork"},
        "hollandhospital": {"tier": "Warm", "touch": "2026-06-26T15:54:12+00:00",
                            "account_id": "abm_hollandhospital"},
    }
    board = [
        # 1. re-key artifact: csv twin of notified company, no new touch
        _acct("csv_ivy_rehab_network", "Ivy Rehab Network", tier="Hot",
              touch="2026-06-23T00:14:44+00:00"),
        # 2. stale history: imported yesterday, newest touch in April
        _acct("csv_healthcare_legal_solutions", "Healthcare Legal Solutions LLC",
              tier="Hot", touch="2026-04-09T00:00:00+00:00"),
        # 3. identity-downgrade artifact (Holland): csv twin at LOWER tier than
        #    the company's notified state
        _acct("csv_holland_hospital", "Holland Hospital", tier="Some",
              touch="2026-07-07T19:00:00+00:00"),
        # 4. genuine: fresh engagement, never notified
        _acct("csv_university_of_maryland_medical_system",
              "University of Maryland Medical System", tier="Some",
              touch="2026-07-09T17:01:12+00:00"),
    ]
    due = notify.accounts_to_notify(board, ledger, cutoff="2026-06-25")
    assert [d["account"]["name"] for d in due] == ["University of Maryland Medical System"]


def test_ledger_key_id_as_name_falls_back_to_id():
    """The board's display fallback sets name = account_id when unresolvable;
    that must NOT be normalized into a garbage company key."""
    assert notify.ledger_key({"name": "abm_dueco", "account_id": "abm_dueco"}) == "abm_dueco"
    tier, _ = notify._ledger_lookup({"abm_dueco": {"tier": "Hot", "touch": None}},
                                    {"name": "abm_dueco", "account_id": "abm_dueco",
                                     "tier": "Hot", "last_touch": None})
    assert tier == "Hot"


def test_same_pass_twins_fire_once_as_strongest():
    """QA panel blocker: id twins of one company in the SAME batch must be
    gated as one company — previously both fired (AE + SDR cards same run)."""
    board = [
        _acct("csv_new_ortho_partners", "New Ortho Partners", tier="Hot",
              touch="2026-07-08T00:00:00+00:00"),
        _acct("abm_neworthopartners", "New Ortho Partners", tier="Warm",
              touch="2026-07-09T00:00:00+00:00"),
    ]
    due = notify.accounts_to_notify(board, {}, cutoff="2026-06-25")
    assert len(due) == 1
    assert due[0]["tier"] == "Hot" and due[0]["role"] == "ae"


def test_record_notified_never_downgrades():
    """QA panel blocker: a weaker twin's write must not downgrade the
    company's recorded state (seed + send paths both use this helper)."""
    led = {}
    notify.record_notified(led, {"name": "New Ortho Partners",
                                 "account_id": "csv_new_ortho_partners"},
                           "Hot", "2026-07-08T00:00:00+00:00")
    notify.record_notified(led, {"name": "New Ortho Partners",
                                 "account_id": "abm_neworthopartners"},
                           "Warm", "2026-07-09T00:00:00+00:00")
    key = notify.company_key("New Ortho Partners")
    assert led[key]["tier"] == "Hot"                    # not downgraded
    # a genuinely stronger write still upgrades
    notify.record_notified(led, {"name": "New Ortho Partners",
                                 "account_id": "csv_new_ortho_partners"},
                           "Hot", "2026-07-10T00:00:00+00:00")
    assert led[key]["touch"] == "2026-07-10T00:00:00+00:00"


def test_due_queue_orders_newest_touch_first():
    """2026-07-10 flood: the due queue was board/score order, so the one
    genuinely-new account ranked #118 of 154 and the send cap delivered 20
    stale artifacts instead. Newest engagement must always be card #1."""
    board = [
        _acct("csv_old_backlog", "Old Backlog Co", tier="Hot",
              touch="2026-06-26T00:00:00+00:00"),
        _acct("csv_fresh_today", "Fresh Today Co", tier="Some",
              touch="2026-07-10T13:00:00+00:00"),
        _acct("csv_mid_week", "Mid Week Co", tier="Warm",
              touch="2026-07-08T09:00:00+00:00"),
    ]
    due = notify.accounts_to_notify(board, {}, cutoff="2026-06-25")
    assert [d["account"]["name"] for d in due] == [
        "Fresh Today Co", "Mid Week Co", "Old Backlog Co"]


def test_merge_ledgers_strongest_and_legacy_strings():
    """merge_ledgers keeps the strongest state per key and survives legacy
    bare-string values on either side (QA verify pass, 2026-07-10)."""
    base = {"a": "Warm", "b": {"tier": "Hot", "touch": "2026-07-01T00:00:00+00:00"}}
    over = {"a": {"tier": "Hot", "touch": None}, "b": "Some",
            "c": {"tier": "Some", "touch": "2026-07-10T00:00:00+00:00"}}
    m = notify.merge_ledgers(base, over)
    assert notify._ledger_entry(m["a"])[0] == "Hot"     # overlay wins on tier
    assert notify._ledger_entry(m["b"])[0] == "Hot"     # base keeps stronger
    assert notify._ledger_entry(m["c"])[0] == "Some"    # new key adopted


def test_state_advance_reposts_even_after_test_memory():
    """The other half of 'ONCE per state': after a test send is recorded, a
    genuine tier RISE (or hot + strictly newer touch) must fire again."""
    test_led = {"freshco": {"tier": "Some", "touch": "2026-07-10T13:00:00+00:00"}}
    # same state -> silent
    board = [_acct("csv_freshco", "Fresh Co", tier="Some",
                   touch="2026-07-10T13:00:00+00:00")]
    assert notify.accounts_to_notify(board, test_led, cutoff="2026-06-25") == []
    # tier rise -> fires again
    board[0]["tier"] = "Warm"
    due = notify.accounts_to_notify(board, test_led, cutoff="2026-06-25")
    assert len(due) == 1 and due[0]["reason"] == "rose"
    # hot + strictly newer touch -> fires again
    test_led["freshco"] = {"tier": "Hot", "touch": "2026-07-10T13:00:00+00:00"}
    board[0]["tier"] = "Hot"
    board[0]["last_touch"] = "2026-07-10T15:00:00+00:00"
    due = notify.accounts_to_notify(board, test_led, cutoff="2026-06-25")
    assert len(due) == 1 and due[0]["reason"] == "hot_activity"


def test_due_sort_survives_none_and_naive_touches():
    board = [
        _acct("csv_naive", "Naive Co", tier="Warm", touch="2026-07-09T09:00:00"),
        _acct("csv_fresh", "Fresh Co", tier="Some", touch="2026-07-10T13:00:00+00:00"),
    ]
    due = notify.accounts_to_notify(board, {}, cutoff="2026-06-25")
    assert [d["account"]["name"] for d in due] == ["Fresh Co", "Naive Co"]
