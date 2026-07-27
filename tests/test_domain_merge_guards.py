"""Domain-merge guards (2026-07-27 audit): name patterns never merge two
companies whose domains provably disagree, and the site-lookup verifier is the
only thing that can say two domains are the same organization.

The replayed incidents: Healthfirst NY (healthfirst.org) fused onto Health
First FL (hf.org); 154 national-CHS (chs.net) contacts on a Wisconsin FQHC
(chsofwi.org); Radiology Partners collapsing onto Radiology Inc via the
degenerate 'radiology' key; email.chop.edu defeating chop.edu domain matching.
"""

from __future__ import annotations

from auto_search.abm.matcher import AbmIndex
from auto_search.abm.models import TargetAccount
from auto_search.engagement import identity, site_verify
from auto_search.engagement.cross import CrossIndex
from auto_search.normalize import registrable_domain

# ── registrable collapse ─────────────────────────────────────────────────


def test_registrable_collapses_subdomains():
    assert registrable_domain("email.chop.edu") == "chop.edu"
    assert registrable_domain("nsmtp.kp.org") == "kp.org"
    assert registrable_domain("chop.edu") == "chop.edu"


def test_registrable_keeps_country_multipart():
    assert registrable_domain("foo.co.uk") == "foo.co.uk"
    assert registrable_domain("www.foo.co.uk") == "foo.co.uk"


# ── cross: the domain-contradiction veto ─────────────────────────────────

HF_SCORED = [{"account_id": "acc_healthfirst", "name": "Health First",
              "domain": "hf.org"}]
HF_ABM = [{"name": "Healthfirst", "domain": "healthfirst.org"}]


def test_healthfirst_contact_never_lands_on_florida_account():
    """The live mis-merge: a healthfirst.org contact name-matched onto the
    hf.org scored account. With the veto it stays unresolved."""
    idx = CrossIndex(HF_SCORED, [])
    m = idx.match(company="Healthfirst", email="abardavid@healthfirst.org")
    assert m is None


def test_conflicting_sibling_link_refused():
    """The ABM target (healthfirst.org) must NOT redirect its hits onto the
    same-named scored account with a different domain."""
    idx = CrossIndex(HF_SCORED, HF_ABM)
    m = idx.match(company="Healthfirst", email="mhecht@healthfirst.org")
    assert m is not None
    assert m.account_id.startswith("abm_")     # its own target, not the FL id


def test_verified_same_pair_relaxes_the_veto():
    pair = site_verify.pair_key("hf.org", "healthfirst.org")
    idx = CrossIndex(HF_SCORED, [], same_pairs={pair})
    m = idx.match(company="Healthfirst", email="a@healthfirst.org")
    assert m is not None and m.account_id == "acc_healthfirst"


def test_domain_tier_match_unaffected_by_veto():
    idx = CrossIndex(HF_SCORED, [])
    m = idx.match(company="Health First", email="anne@hf.org")
    assert m is not None and m.tier == "domain"


def test_contact_without_email_still_name_matches():
    """No email evidence = no contradiction = name matching keeps working."""
    idx = CrossIndex(HF_SCORED, [])
    m = idx.match(company="Health First")
    assert m is not None and m.account_id == "acc_healthfirst"


def test_chs_national_contact_refused_from_wisconsin_fqhc():
    scored = [{"account_id": "abm_communityhealthsystems",
               "name": "COMMUNITY HEALTH SYSTEMS, INC.", "domain": "chsofwi.org"}]
    idx = CrossIndex(scored, [])
    assert idx.match(company="Community Health Systems",
                     email="jane@chs.net") is None


def test_subdomain_email_now_domain_matches():
    scored = [{"account_id": "abm_chop", "name": "Childrens Hospital of "
               "Philadelphia", "domain": "email.chop.edu"}]
    idx = CrossIndex(scored, [])
    m = idx.match(company="CHOP", email="doc@chop.edu")
    assert m is not None and m.tier == "domain"


# ── cross: degenerate single-word keys ───────────────────────────────────


def test_radiology_partners_does_not_merge_with_radiology_inc():
    scored = [{"account_id": "csv_radiology_inc", "name": "Radiology Inc",
               "domain": "radiologyincir.com"}]
    idx = CrossIndex(scored, [])
    assert idx.match(company="Radiology Partners") is None


def test_suffix_variant_of_distinctive_name_still_matches():
    scored = [{"account_id": "csv_healogics", "name": "Healogics Inc",
               "domain": "healogics.com"}]
    idx = CrossIndex(scored, [])
    m = idx.match(company="Healogics")
    assert m is not None and m.account_id == "csv_healogics"


def test_the_prefix_variants_match():
    scored = [{"account_id": "csv_valley", "name": "The Valley Hospital",
               "domain": "valleyhealth.com"}]
    idx = CrossIndex(scored, [])
    m = idx.match(company="Valley Hospital")
    assert m is not None and m.account_id == "csv_valley"


def test_index_collisions_are_observable():
    """Two distinct same-named companies (Methodist Dallas vs Omaha class):
    the shadowed one must at least be visible in the collision report."""
    scored = [{"account_id": "a1", "name": "Methodist Health System",
               "domain": "methodisthealthsystem.org"},
              {"account_id": "a2", "name": "Methodist Health System",
               "domain": "mhs.team"}]
    idx = CrossIndex(scored, [])
    assert any("a2" in c and "a1" in c for c in idx.collisions)


# ── abm matcher: review-cap on conflicting domains ───────────────────────


def _target(**kw):
    base = {"name": "Healthfirst", "domain": "healthfirst.org", "state": "NY",
            "aliases": [], "keys": ["healthfirst"], "segment": "Payer"}
    base.update(kw)
    return TargetAccount(**{k: v for k, v in base.items()
                            if k in TargetAccount.__dataclass_fields__}) \
        if hasattr(TargetAccount, "__dataclass_fields__") else TargetAccount(**base)


def test_matcher_name_state_with_domain_conflict_is_review_not_confirmed():
    t = _target()
    idx = AbmIndex([t])
    m = idx.match("Healthfirst", domain="hf.org", states=["NY"])
    assert m is not None and m.tier == "review"


def test_matcher_name_state_without_conflict_still_confirms():
    t = _target()
    idx = AbmIndex([t])
    m = idx.match("Healthfirst", domain="healthfirst.org", states=["NY"])
    assert m is not None and m.tier == "confirmed"


# ── site_verify: ladder + cache ──────────────────────────────────────────


def _fake_fetch(mapping):
    def fetch(domain, **_kw):
        return mapping.get(domain, site_verify.SiteIdentity(domain=domain,
                                                            error="nope"))
    return fetch


def test_registrable_equal_is_same_without_fetching():
    v = site_verify.verify_same_company("A", "email.chop.edu", "B", "chop.edu",
                                        fetch=_fake_fetch({}))
    assert (v.verdict, v.confidence) == ("same", "high")


def test_redirect_convergence_is_same_high():
    m = {"kaiserpermanente.org": site_verify.SiteIdentity(
            "kaiserpermanente.org", final_host="healthy.kaiserpermanente.org"),
         "kp.org": site_verify.SiteIdentity(
            "kp.org", final_host="healthy.kaiserpermanente.org")}
    v = site_verify.verify_same_company("Kaiser", "kaiserpermanente.org",
                                        "Kaiser", "kp.org", fetch=_fake_fetch(m))
    assert (v.verdict, v.confidence, v.method) == ("same", "high",
                                                   "redirect-convergence")


def test_no_signal_is_unknown_never_same():
    m = {"healthfirst.org": site_verify.SiteIdentity(
            "healthfirst.org", final_host="healthfirst.org", title="NY insurer"),
         "hf.org": site_verify.SiteIdentity(
            "hf.org", final_host="hf.org", title="Health First FL")}
    v = site_verify.verify_same_company("Healthfirst", "healthfirst.org",
                                        "Health First", "hf.org",
                                        fetch=_fake_fetch(m))
    assert v.verdict == "unknown"


class _SettingsRepo:
    def __init__(self):
        self._s = {}

    def get_setting(self, k):
        return self._s.get(k)

    def set_setting(self, k, v):
        self._s[k] = v


def test_human_verdict_never_overwritten_by_auto():
    repo = _SettingsRepo()
    site_verify.store_verdict(repo, "a.com", "b.com",
                              site_verify.Verdict("different", "high", "human"),
                              decided_by="human")
    site_verify.store_verdict(repo, "a.com", "b.com",
                              site_verify.Verdict("same", "high", "auto"))
    v = site_verify.cached_verdict(repo, "a.com", "b.com")
    assert v.verdict == "different"
    assert site_verify.pair_key("a.com", "b.com") in \
        site_verify.verified_different_pairs(repo)


def test_low_confidence_same_is_not_a_merge_licence():
    repo = _SettingsRepo()
    site_verify.store_verdict(repo, "a.com", "b.com",
                              site_verify.Verdict("same", "low", "adjudicated"))
    assert site_verify.verified_same_pairs(repo) == set()


# ── heal: evidence checks ────────────────────────────────────────────────


class _HealRepo(_SettingsRepo):
    def __init__(self, accounts, contacts, events=()):
        super().__init__()
        self._accounts = accounts
        self._contacts = contacts
        self._events = list(events)
        self.rekeys = []

    def engaged_accounts(self):
        return self._accounts

    def contacts(self, **_kw):
        return self._contacts

    def event_account_ids(self):
        return {e["account_id"] for e in self._events}

    def rekey_account(self, old, new):
        self.rekeys.append((old, new))
        return {"events": 1, "contacts": 1, "activations": 0}


class _Scoring:
    def __init__(self, rows):
        self._rows = rows

    def list_accounts(self):
        return self._rows


class _Discovery:
    def __init__(self, rows):
        self._rows = rows

    def abm_targets(self):
        return self._rows


def test_heal_missing_domain_twin_with_conflicting_email_evidence_goes_manual():
    """csv row has no domain, but its contacts' corporate emails contradict the
    canon's identity — the old code auto-merged this on name alone."""
    repo = _HealRepo(
        accounts=[{"account_id": "csv_healthfirst"},
                  {"account_id": "abm_healthfirst"}],
        contacts=[{"account_id": "csv_healthfirst", "email": "x@hf.org"},
                  {"account_id": "abm_healthfirst",
                   "email": "y@healthfirst.org"}])
    scoring = _Scoring([{"account_id": "csv_healthfirst", "name": "Healthfirst"}])
    disc = _Discovery([{"name": "Healthfirst"}])   # target row without domain
    rep = identity.heal_identity_splits(repo, scoring, disc, dry_run=True)
    assert rep["merged"] == {}
    assert any("evidence disagrees" in m["why"] for m in rep["manual"])


def test_heal_agreeing_email_evidence_still_merges():
    repo = _HealRepo(
        accounts=[{"account_id": "csv_summa"}, {"account_id": "abm_summahealth"}],
        contacts=[{"account_id": "csv_summa", "email": "a@summahealth.org"},
                  {"account_id": "abm_summahealth", "email": "b@summahealth.org"}])
    scoring = _Scoring([{"account_id": "csv_summa", "name": "Summa Health"}])
    disc = _Discovery([{"name": "Summa Health"}])
    rep = identity.heal_identity_splits(repo, scoring, disc, dry_run=True)
    assert rep["merged"] == {"abm_summahealth": "csv_summa"}


def test_heal_conflicting_domains_with_verified_same_merges():
    repo = _HealRepo(
        accounts=[{"account_id": "csv_kaiser"}, {"account_id": "abm_kaiserpermanente"}],
        contacts=[])
    site_verify.store_verdict(repo, "kp.org", "kaiserpermanente.org",
                              site_verify.Verdict("same", "high",
                                                  "redirect-convergence"))
    scoring = _Scoring([{"account_id": "csv_kaiser", "name": "Kaiser Permanente",
                         "domain": "kp.org"}])
    disc = _Discovery([{"name": "Kaiser Permanente",
                        "domain": "kaiserpermanente.org"}])
    rep = identity.heal_identity_splits(repo, scoring, disc, dry_run=True)
    assert rep["merged"] == {"abm_kaiserpermanente": "csv_kaiser"}
    assert rep["manual"] == []


def test_heal_verified_different_stays_separate_and_leaves_manual_queue():
    repo = _HealRepo(
        accounts=[{"account_id": "acc_healthfirst"}, {"account_id": "abm_healthfirst"}],
        contacts=[])
    site_verify.store_verdict(repo, "hf.org", "healthfirst.org",
                              site_verify.Verdict("different", "low",
                                                  "adjudicated"))
    scoring = _Scoring([{"account_id": "acc_healthfirst", "name": "Health First",
                         "domain": "hf.org"}])
    disc = _Discovery([{"name": "Healthfirst", "domain": "healthfirst.org"}])
    rep = identity.heal_identity_splits(repo, scoring, disc, dry_run=True)
    assert rep["merged"] == {}
    assert rep["manual"] == []          # resolved: keep separate, stop nagging


def test_heal_sees_deprecated_kind_only_accounts():
    repo = _HealRepo(
        accounts=[{"account_id": "csv_intermountain"}],
        contacts=[],
        events=[{"account_id": "abm_intermountainhealth"}])
    scoring = _Scoring([{"account_id": "csv_intermountain",
                         "name": "Intermountain Health"}])
    disc = _Discovery([{"name": "Intermountain Health"}])
    rep = identity.heal_identity_splits(repo, scoring, disc, dry_run=True)
    assert rep["merged"] == {"abm_intermountainhealth": "csv_intermountain"}


def test_sibling_hop_cannot_bypass_the_veto():
    """Review 2026-07-27 (reproduced pre-fix): when the ABM target row has NO
    domain, the build-time sibling gate can't see a conflict — a contact vetoed
    against the scored account must NOT re-reach it through the hop. The
    contact stays on the abm_ tile (which the heal will manual-queue, never
    merge)."""
    from auto_search.engagement.cross import CrossIndex
    idx = CrossIndex(
        scored=[{"account_id": "acc_healthfirst", "name": "Health First",
                 "domain": "hf.org"}],
        abm_targets=[{"name": "Healthfirst", "domain": None}])
    m = idx.match(company="Healthfirst", email="x@healthfirst.org")
    assert m is not None
    assert m.account_id == "abm_healthfirst"      # NOT acc_healthfirst
    # and a compatible contact still takes the merged-row sibling
    m2 = idx.match(company="Healthfirst", email="y@hf.org")
    assert m2 is not None and m2.account_id == "acc_healthfirst"


def test_exact_host_outranks_registrable_collapse():
    """Review 2026-07-27: two subsidiaries on one parent registrable domain
    each keep their own exact-host match; the collapse is only a fallback."""
    from auto_search.engagement.cross import CrossIndex
    idx = CrossIndex(
        scored=[{"account_id": "acc_mercy", "name": "Mercy Trinity",
                 "domain": "mercy.trinityhealth.org"},
                {"account_id": "acc_stjoes", "name": "St Joes Trinity",
                 "domain": "stjoes.trinityhealth.org"}],
        abm_targets=[])
    m = idx.match(email="a@stjoes.trinityhealth.org")
    assert m is not None and m.account_id == "acc_stjoes"
    m2 = idx.match(email="b@mercy.trinityhealth.org")
    assert m2 is not None and m2.account_id == "acc_mercy"


def test_vetoed_pairs_are_recorded_for_verification():
    """Review 2026-07-27: every veto records its (contact, account) domain pair
    so the sync's site-verify pass can rule same/different — a legitimate
    second-corporate-domain system self-resolves instead of staying
    unresolved forever."""
    from auto_search.engagement.cross import CrossIndex
    idx = CrossIndex(
        scored=[{"account_id": "acc_advent", "name": "AdventHealth",
                 "domain": "adventhealth.com"}],
        abm_targets=[])
    assert idx.match(company="AdventHealth", email="x@ah.org") is None
    assert ("ah.org", "adventhealth.com", "AdventHealth") in idx.vetoed_pairs
    # and once the pair is verified-same, the SAME shape of index attaches it
    idx2 = CrossIndex(
        scored=[{"account_id": "acc_advent", "name": "AdventHealth",
                 "domain": "adventhealth.com"}],
        abm_targets=[], same_pairs={"adventhealth.com|ah.org"})
    m = idx2.match(company="AdventHealth", email="x@ah.org")
    assert m is not None and m.account_id == "acc_advent"


def test_build_time_sibling_refusal_is_also_queued_for_verification():
    """Live 2026-07-27: the first two production refusals (den.health vs
    denverhealth.org, scanhealthplan.com vs thescangroup.org) are both
    plausibly the SAME org — the build-time gate must queue them for
    site verification, not just refuse them silently forever."""
    from auto_search.engagement.cross import CrossIndex
    idx = CrossIndex(
        scored=[{"account_id": "acc_denverhealth", "name": "Denver Health",
                 "domain": "denverhealth.org"}],
        abm_targets=[{"name": "Denver Health", "domain": "den.health"}])
    assert ("den.health", "denverhealth.org", "Denver Health") in idx.vetoed_pairs
    # once verified same, the sibling link is established normally
    idx2 = CrossIndex(
        scored=[{"account_id": "acc_denverhealth", "name": "Denver Health",
                 "domain": "denverhealth.org"}],
        abm_targets=[{"name": "Denver Health", "domain": "den.health"}],
        same_pairs={"den.health|denverhealth.org"})
    m = idx2.match(company="Denver Health", email="x@den.health")
    assert m is not None and m.account_id == "acc_denverhealth"
