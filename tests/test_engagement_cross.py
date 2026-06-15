"""Cross to scored + ABM accounts (Milestone D) — deterministic matching."""

from auto_search.engagement.cross import AccountMatch, CrossIndex

SCORED = [
    {"account_id": "acc_christus", "name": "CHRISTUS Health", "domain": "christushealth.org"},
    {"account_id": "acc_ortho", "name": "OrthoIndy", "domain": None},
]
ABM = [
    {"name": "CHRISTUS Health", "keys": ["christushealth"], "domain": "christushealth.org"},
    {"name": "Newport Healthcare", "keys": ["newporthealthcare"], "domain": "newporthealthcare.com"},
]


def _idx():
    return CrossIndex(SCORED, ABM)


def test_domain_match_to_scored():
    m = _idx().match(domain="christushealth.org")
    assert m.account_id == "acc_christus" and m.tier == "domain"
    assert set(m.lists) == {"scored", "abm"}        # also an ABM target -> merged tags


def test_name_match_to_scored_when_no_domain():
    m = _idx().match(company="OrthoIndy")
    assert m == AccountMatch("acc_ortho", "OrthoIndy", "name", ("scored",))


def test_abm_only_match_gets_synthetic_id():
    m = _idx().match(domain="newporthealthcare.com")
    assert m.account_id == "abm_newporthealthcare"
    assert m.lists == ("abm",) and m.tier == "domain"


def test_email_domain_used_when_no_explicit_domain():
    m = _idx().match(email="gloria@christushealth.org")
    assert m.account_id == "acc_christus" and m.tier == "domain"


def test_personal_domain_falls_through_to_company_name():
    # a gmail contact whose company is an ABM target -> name match (not domain)
    m = _idx().match(email="someone@gmail.com", company="Newport Healthcare")
    assert m.account_id == "abm_newporthealthcare" and m.tier == "name"


def test_no_match_returns_none():
    assert _idx().match(company="Totally Unknown Co", domain="nowhere.example") is None


def test_scored_wins_over_abm_for_account_id():
    # a company on both lists resolves to the scored id, tagged both
    m = _idx().match(company="CHRISTUS Health")
    assert m.account_id == "acc_christus"
    assert set(m.lists) == {"scored", "abm"}


def test_build_index_from_repos():
    class _S:
        def list_accounts(self):
            return SCORED

    class _D:
        def abm_targets(self):
            return ABM

    from auto_search.engagement.cross import build_index
    idx = build_index(_S(), _D())
    assert idx.match(domain="christushealth.org").account_id == "acc_christus"
