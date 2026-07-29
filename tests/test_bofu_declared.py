"""BOFU leads must never be silently lost (MAR2-50, 2026-07-28).

Fatma Mirza declared "Mount Sinai Health System" on our contact form and SFDC
delivered the lead — then OUR OWN safety nets threw her away twice: cross.py's
domain-contradiction veto refused the name match ("contact cornell.edu vs
account mountsinai.org"), and run_sfdc_sync's persist_unmatched=False then
discarded the vetoed contact with no trace (same mechanism ate the Anthem KLAS
lead, and ~1,354 unresolved/run vanish silently).

Product rule (Sunny, 2026-07-28): BOFU = the human DECLARED their company on
our form; SFDC vouches. The domain-contradiction veto exists for INFERRED
matches (ad reactors, scraped engagers) — declared ones bind, tagged
match_tier='name+bofu'. Contacts that STILL don't match persist as unresolved
(current window only, never the backlog) and BOFU-grade drops fire one
consolidated, throttled ops alert naming the leads.
"""

from __future__ import annotations

from auto_search.db.engagement_repository import EngagementJsonRepository
from auto_search.engagement import sync as sync_mod
from auto_search.engagement.cross import CrossIndex


class _Scoring:
    def list_accounts(self):
        return [{"account_id": "acc_mount_sinai", "name": "Mount Sinai Health System",
                 "domain": "mountsinai.org"}]


class _Discovery:
    def abm_targets(self):
        return []


class _SfdcClient:
    def __init__(self, leads, low_intent=None):
        self._leads = leads
        self._lo = low_intent or []

    def iter_high_intent_leads(self, *, since="2026-01-01"):
        yield from self._leads

    def iter_tradeshow_leads(self, *, since="2026-01-01"):
        yield from []

    def iter_low_intent_leads(self, *, since="2026-01-01"):
        yield from self._lo

    def iter_meetings(self, *, days=180):
        yield from []


def _fatma(**kw):
    lead = {"Id": "00QFATMA", "FirstName": "Fatma", "LastName": "Mirza",
            "Company": "Mount Sinai Health System", "Email": "fym4@cornell.edu",
            "BN_Email_Domain__c": "cornell.edu", "LeadSource": "Sales Contact Form",
            "Status": "New", "CreatedDate": "2026-07-27T15:00:00.000+0000"}
    lead.update(kw)
    return lead


# ── the veto, cross-level ───────────────────────────────────────────────


def _index():
    return CrossIndex(_Scoring().list_accounts(), [])


def test_declared_company_binds_past_the_domain_veto_as_name_bofu():
    """Fatma's shape: personal/academic email + declared company must bind."""
    m = _index().match(company="Mount Sinai Health System",
                       email="fym4@cornell.edu", trust_declared=True)
    assert m is not None
    assert m.account_id == "acc_mount_sinai"
    assert m.tier == "name+bofu"


def test_inferred_contact_with_conflicting_domain_is_still_vetoed():
    """The veto is untouched for every non-declared path (ad reactors etc.)."""
    m = _index().match(company="Mount Sinai Health System",
                       email="someone@healthfirst.org")
    assert m is None


def test_compatible_domain_declared_match_stays_a_plain_name_match():
    """No contradiction -> nothing to override; the tier stays 'name'
    (a personal-provider email carries no usable domain, so no veto)."""
    m = _index().match(company="Mount Sinai Health System",
                       email="fatma@gmail.com", trust_declared=True)
    assert m is not None and m.tier == "name"


# ── the sync path end-to-end ────────────────────────────────────────────


def test_fatma_shape_binds_through_run_sfdc_sync(tmp_path):
    repo = EngagementJsonRepository(path=str(tmp_path / "e.json"))
    stats = sync_mod.run_sfdc_sync(
        engagement_repo=repo, scoring_repo=_Scoring(), discovery_repo=_Discovery(),
        client=_SfdcClient([_fatma()]), now="2026-07-28T12:00:00+00:00")
    assert stats["matched_contacts"] == 1
    contacts = repo.contacts(account_id="acc_mount_sinai")
    assert len(contacts) == 1
    assert contacts[0]["email"] == "fym4@cornell.edu"
    assert contacts[0]["match_tier"] == "name+bofu"
    events = repo.events_for_account("acc_mount_sinai")
    assert len(events) == 1 and events[0]["kind"] == "high_intent_lead"


def test_low_intent_lead_does_not_get_the_declared_override(tmp_path):
    """Only the BOFU-class (high-intent form) leg carries declared trust."""
    repo = EngagementJsonRepository(path=str(tmp_path / "e.json"))
    lo = [_fatma(Id="00QLO", LeadSource="6 UM Trends 2026 | TOFU")]
    sync_mod.run_sfdc_sync(
        engagement_repo=repo, scoring_repo=_Scoring(), discovery_repo=_Discovery(),
        client=_SfdcClient([], low_intent=lo), now="2026-07-28T12:00:00+00:00")
    assert repo.contacts(account_id="acc_mount_sinai") == []


def test_unmatched_bofu_persists_unresolved_and_alerts(tmp_path, monkeypatch):
    """The Anthem class: still no match -> unresolved contact (not silence)
    plus ONE consolidated ops alert naming the lead."""
    posted = []
    monkeypatch.setattr("auto_search.ops.alerts.post_ops_alert",
                        lambda **kw: posted.append(kw) or True)
    repo = EngagementJsonRepository(path=str(tmp_path / "e.json"))
    lead = _fatma(Id="00QANTHEM", FirstName="Kay", LastName="Lee",
                  Company="Anthem", Email="kay.lee@anthem.com",
                  BN_Email_Domain__c="anthem.com",
                  CreatedDate="2026-07-27T09:00:00.000+0000")
    stats = sync_mod.run_sfdc_sync(
        engagement_repo=repo, scoring_repo=_Scoring(), discovery_repo=_Discovery(),
        client=_SfdcClient([lead]), now="2026-07-28T12:00:00+00:00")
    assert stats["matched_contacts"] == 0
    unresolved = repo.contacts(unresolved_only=True)
    assert len(unresolved) == 1 and unresolved[0]["email"] == "kay.lee@anthem.com"
    assert len(posted) == 1
    alert = posted[0]
    assert alert["kind"] == "sfdc-bofu-unresolved"
    assert "Kay Lee" in alert["detail"] and "Anthem" in alert["detail"]


def test_unmatched_backlog_outside_window_is_not_persisted(tmp_path, monkeypatch):
    """The ~1,354 historical unresolved must NOT flood in: only leads from the
    current sync window persist, and old drops never alert."""
    posted = []
    monkeypatch.setattr("auto_search.ops.alerts.post_ops_alert",
                        lambda **kw: posted.append(kw) or True)
    repo = EngagementJsonRepository(path=str(tmp_path / "e.json"))
    old = _fatma(Id="00QOLD", Company="Anthem", Email="old@anthem.com",
                 BN_Email_Domain__c="anthem.com",
                 CreatedDate="2026-01-15T09:00:00.000+0000")
    sync_mod.run_sfdc_sync(
        engagement_repo=repo, scoring_repo=_Scoring(), discovery_repo=_Discovery(),
        client=_SfdcClient([old]), now="2026-07-28T12:00:00+00:00")
    assert repo.contacts(unresolved_only=True) == []
    assert posted == []


def test_bofu_alert_is_throttled_and_not_renamed_on_resync(tmp_path, monkeypatch):
    """A re-sync must not re-alert the same already-persisted lead."""
    posted = []
    monkeypatch.setattr("auto_search.ops.alerts.post_ops_alert",
                        lambda **kw: posted.append(kw) or True)
    repo = EngagementJsonRepository(path=str(tmp_path / "e.json"))
    lead = _fatma(Id="00QANTHEM", Company="Anthem", Email="kay.lee@anthem.com",
                  BN_Email_Domain__c="anthem.com",
                  CreatedDate="2026-07-27T09:00:00.000+0000")
    for _ in range(2):
        sync_mod.run_sfdc_sync(
            engagement_repo=repo, scoring_repo=_Scoring(),
            discovery_repo=_Discovery(), client=_SfdcClient([lead]),
            now="2026-07-28T12:00:00+00:00")
    assert len(posted) == 1
