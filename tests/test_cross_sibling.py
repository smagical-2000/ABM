"""MAR2-32 v2: variant/alias names of a SCORED company must cross to the scored
id — the re-mint class. On 2026-07-14 the daily sync re-crossed leads whose
company read "Summa Health" (vs scored name "Summa Health System"): the scored
index missed, the ABM alias hit, and an abm_ twin was re-minted for an
already-scored company. The sibling rebind kills that at the data layer, so
even a writer with no self-heal cannot split a scored company's identity."""
from auto_search.engagement.cross import CrossIndex

SCORED = [{"account_id": "csv_summa_health_system", "name": "Summa Health System",
           "domain": "summahealth.org"}]
ABM = [{"name": "Summa Health System", "domain": "summahealth.org",
        "keys": ["summahealthsystem", "summahealth"]}]   # alias-expanded target


def test_variant_name_crosses_to_scored_id_not_abm_twin():
    idx = CrossIndex(SCORED, ABM)
    m = idx.match(company="Summa Health")          # the variant that re-minted
    assert m is not None
    assert m.account_id == "csv_summa_health_system"
    assert set(m.lists) == {"scored", "abm"}       # merged-row rule


def test_exact_scored_name_still_wins_directly():
    idx = CrossIndex(SCORED, ABM)
    m = idx.match(company="Summa Health System!")
    assert m.account_id == "csv_summa_health_system"


def test_abm_only_company_still_mints_abm_id():
    """No scored sibling → behavior unchanged: abm-only match keeps the
    synthetic abm_ id (self-heals to the scored id once scored)."""
    idx = CrossIndex([], ABM)
    m = idx.match(company="Summa Health")
    assert m.account_id == "abm_summahealthsystem"
    assert m.lists == ("abm",)


def test_domain_sibling_rebinds_too():
    """Scored row matches the abm target by DOMAIN (name differs entirely):
    an alias hit must still resolve to the scored id."""
    scored = [{"account_id": "csv_shs", "name": "SHS Inc",
               "domain": "summahealth.org"}]
    idx = CrossIndex(scored, ABM)
    m = idx.match(company="Summa Health")
    assert m.account_id == "csv_shs"
    assert set(m.lists) == {"scored", "abm"}
