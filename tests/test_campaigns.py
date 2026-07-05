"""Campaign automation (Phase 3) — catalog routing, the pure enrollment rule,
the runner (dry vs live, idempotency, 409s, caps), and the ledger repo.

Fakes for the Reply.io client + scoring/engagement repos; the campaign ledger
runs on the real JSON impl so both sides of the dual-repo contract stay honest.
"""

import pytest

from auto_search.campaigns import catalog, enroll, runner
from auto_search.db.campaign_repository import CampaignJsonRepository

# ── fixtures ──────────────────────────────────────────────────────────


def _scored(account_id="acc_rothman", name="Rothman Orthopaedics", *,
            state="scored", tier_band="high", tier_label="High Fit",
            segment="specialty", sub_segment="Orthopedics", signals=None):
    return {"account_id": account_id, "name": name, "state": state,
            "tier_band": tier_band, "tier_label": tier_label, "segment": segment,
            "sub_segment": sub_segment, "framework": "Specialties",
            "discovery_signals": signals or []}


def _hot_signals():
    # A new exec is 65 base + recency; hot on its own (priority.py).
    from datetime import UTC, datetime
    return [{"signal_type": "leadership_change",
             "observed_at": datetime.now(UTC).isoformat(), "payload": {}}]


class _FakeScoring:
    def __init__(self, rows):
        self._rows = rows

    def list_accounts(self):
        return self._rows


class _FakeEngagement:
    """Just the two reads the runner needs: heat rollup + matched contacts."""

    def __init__(self, engaged=None, contacts=None):
        self._engaged = engaged or []
        self._contacts = contacts or []

    def engaged_accounts(self):
        return self._engaged

    def contacts(self, **_kw):
        return self._contacts


class _FakeReply:
    def __init__(self, conflict=(), fail=()):
        self.conflict, self.fail = set(conflict), set(fail)
        self.calls = []

    async def add_to_campaign(self, *, campaign_id, email, **_kw):
        self.calls.append((campaign_id, email))
        if email in self.fail:
            raise RuntimeError("reply.io down")
        if email in self.conflict:
            return {"status": 409, "detail": "already in a sequence"}
        return {"id": len(self.calls)}


def _contact(ext, email, account_id, *, opted_out=False):
    return {"external_id": ext, "email": email, "account_id": account_id,
            "opted_out": opted_out, "title": "CFO", "company": "Rothman"}


@pytest.fixture
def crepo(tmp_path):
    r = CampaignJsonRepository(path=str(tmp_path / "campaigns.json"))
    r.upsert_sequence("ortho", campaign_id="111", campaign_name="Outbound Ortho")
    return r


# ── catalog: ICP -> sequence key ──────────────────────────────────────


def test_catalog_routes_by_segment_then_subvertical():
    assert catalog.sequence_key_for({"segment": "payer"}) == "payer"
    assert catalog.sequence_key_for({"segment": "health_system"}) == "health_system"
    assert catalog.sequence_key_for(_scored()) == "ortho"
    assert catalog.sequence_key_for(
        _scored(sub_segment="Behavioral Health")) == "behavioral"
    assert catalog.sequence_key_for(
        _scored(sub_segment="", name="Radiology Partners")) == "radiology"
    assert catalog.sequence_key_for(
        _scored(sub_segment="Dermatology", name="Acme Derm")) == "specialty_other"


def test_catalog_name_never_misroutes_health_systems():
    # A hospital with "Radiology" in a department-ish name stays health_system.
    a = {"segment": "health_system", "name": "Northside Hospital Radiology Center"}
    assert catalog.sequence_key_for(a) == "health_system"


# ── enroll: the pure rule ─────────────────────────────────────────────


def test_eligible_requires_fit_and_a_trigger():
    rows = [
        _scored("a1", "Warm+HighFit"),                       # heat trigger
        _scored("a2", "ColdHighFit"),                        # fit alone: NOT eligible
        _scored("a3", "HotIntent", signals=_hot_signals()),  # intent trigger
        _scored("a4", "LowFit", tier_band="low"),            # low fit: never
        _scored("a5", "Queued", state="queued"),             # not scored yet
    ]
    heat = {"a1": 15}                                        # Warm (12-20)
    out = enroll.eligible_accounts(rows, heat)
    ids = {e.account_id for e in out}
    assert ids == {"a1", "a3"}
    by_id = {e.account_id: e for e in out}
    assert "Warm engagement (15 pts)" in by_id["a1"].reasons[1]
    assert "Hot buying intent" in by_id["a3"].reasons
    assert by_id["a1"].sequence_key == "ortho"


def test_eligible_excludes_already_enrolled_and_ranks_by_heat():
    rows = [_scored("a1"), _scored("a2", "Second")]
    heat = {"a1": 25, "a2": 13}
    out = enroll.eligible_accounts(rows, heat, exclude_ids={"a1"})
    assert [e.account_id for e in out] == ["a2"]


def test_plan_contacts_filters_the_unsendable():
    contacts = [
        _contact("c1", "cfo@rothman.com", "a1"),
        _contact("c2", "", "a1"),                            # no email
        _contact("c3", "out@rothman.com", "a1", opted_out=True),
        _contact("c4", "CFO@rothman.com", "a1"),             # duplicate email (case)
        _contact("c5", "done@rothman.com", "a1"),            # already in the ledger
    ]
    planned, skipped = enroll.plan_contacts(contacts, already={"c5"})
    assert [p["contact_ext"] for p in planned] == ["c1"]
    assert skipped == {"no_email": 1, "opted_out": 1, "already": 1,
                       "duplicate": 1, "capped": 0}


# ── runner ────────────────────────────────────────────────────────────


def _world(crepo, *, heat_score=15):
    scoring = _FakeScoring([_scored()])
    engagement = _FakeEngagement(
        engaged=[{"account_id": "acc_rothman", "score": heat_score}],
        contacts=[_contact("c1", "cfo@rothman.com", "acc_rothman"),
                  _contact("c2", "coo@rothman.com", "acc_rothman"),
                  _contact("c3", "other@elsewhere.com", "acc_other")])
    return scoring, engagement


@pytest.mark.asyncio
async def test_dry_run_plans_but_persists_and_sends_nothing(crepo):
    scoring, engagement = _world(crepo)
    reply = _FakeReply()
    res = await runner.run(campaign_repo=crepo, engagement_repo=engagement,
                           scoring_repo=scoring, replyio_client=reply, dry_run=True)
    assert res["stats"]["would_enroll_accounts"] == 1
    assert res["stats"]["would_enroll_contacts"] == 2      # a3 belongs to another account
    assert reply.calls == []                                # nothing sent
    assert crepo.enrollments() == []                        # nothing persisted
    assert res["accounts"][0]["status"] == "dry_run"


@pytest.mark.asyncio
async def test_live_run_enrolls_records_and_is_idempotent(crepo):
    scoring, engagement = _world(crepo)
    reply = _FakeReply(conflict={"coo@rothman.com"})
    res = await runner.run(campaign_repo=crepo, engagement_repo=engagement,
                           scoring_repo=scoring, replyio_client=reply, dry_run=False)
    a = res["accounts"][0]
    assert (a["enrolled"], a["skipped_409"], a["failed"]) == (1, 1, 0)
    rows = crepo.enrollments()
    assert {r["status"] for r in rows} == {"enrolled", "skipped_409"}
    assert all(r["campaign_id"] == "111" for r in rows)

    # Re-run: the account is now in the ledger -> excluded entirely.
    res2 = await runner.run(campaign_repo=crepo, engagement_repo=engagement,
                            scoring_repo=scoring, replyio_client=reply, dry_run=False)
    assert res2["stats"].get("accounts_considered", 0) == 0
    assert len(reply.calls) == 2                            # no new sends


@pytest.mark.asyncio
async def test_live_failure_is_recorded_not_raised(crepo):
    scoring, engagement = _world(crepo)
    reply = _FakeReply(fail={"cfo@rothman.com"})
    res = await runner.run(campaign_repo=crepo, engagement_repo=engagement,
                           scoring_repo=scoring, replyio_client=reply, dry_run=False)
    a = res["accounts"][0]
    assert a["failed"] == 1 and a["enrolled"] == 1
    failed = [r for r in crepo.enrollments() if r["status"] == "failed"]
    assert failed and "reply.io down" in failed[0]["detail"]["error"]
    # A failed contact is NOT terminal: a re-run may retry it (only 409/enrolled
    # are excluded), so the account stays enrolled but c1 is retryable.
    assert crepo.enrolled_for("acc_rothman", "111") == {"c2"}


@pytest.mark.asyncio
async def test_unmapped_sequence_blocks_cleanly(tmp_path):
    crepo = CampaignJsonRepository(path=str(tmp_path / "c.json"))   # no mapping at all
    scoring, engagement = _world(crepo)
    res = await runner.run(campaign_repo=crepo, engagement_repo=engagement,
                           scoring_repo=scoring, replyio_client=_FakeReply(),
                           dry_run=False)
    assert res["stats"]["unmapped_sequence"] == 1
    assert res["accounts"][0]["status"] == "unmapped"
    assert crepo.enrollments() == []


@pytest.mark.asyncio
async def test_account_cap_drips(crepo):
    rows = [_scored(f"a{i}", f"Acct {i}") for i in range(5)]
    scoring = _FakeScoring(rows)
    engagement = _FakeEngagement(
        engaged=[{"account_id": f"a{i}", "score": 30} for i in range(5)],
        contacts=[_contact(f"c{i}", f"p{i}@x{i}.com", f"a{i}") for i in range(5)])
    res = await runner.run(campaign_repo=crepo, engagement_repo=engagement,
                           scoring_repo=scoring, replyio_client=_FakeReply(),
                           dry_run=True, account_cap=2)
    assert res["capped"] is True
    assert res["stats"]["accounts_considered"] == 2
    assert res["eligible_total"] == 5


@pytest.mark.asyncio
async def test_manual_enroll_bypasses_trigger_but_not_the_ledger(crepo):
    scoring, engagement = _world(crepo, heat_score=0)       # cold: auto would skip it
    reply = _FakeReply()
    res = await runner.run(campaign_repo=crepo, engagement_repo=engagement,
                           scoring_repo=scoring, replyio_client=reply, dry_run=False,
                           only_account_id="acc_rothman", trigger="manual")
    a = res["accounts"][0]
    assert a["reasons"] == ["Manually enrolled"] and a["enrolled"] == 2
    assert all(r["trigger"] == "manual" for r in crepo.enrollments())
    # unknown / unscored account -> clean no-op
    res2 = await runner.run(campaign_repo=crepo, engagement_repo=engagement,
                            scoring_repo=scoring, replyio_client=reply, dry_run=False,
                            only_account_id="acc_nope")
    assert res2["stats"] == {"not_scored": 1}


# ── ledger repo (JSON impl) ───────────────────────────────────────────


def test_repo_roundtrip_and_upsert(tmp_path):
    r = CampaignJsonRepository(path=str(tmp_path / "c.json"))
    row = {"account_id": "a1", "contact_ext": "c1", "email": "x@y.com",
           "sequence_key": "ortho", "campaign_id": "111", "status": "enrolled"}
    assert r.add_enrollment(row) is True
    assert r.add_enrollment({**row, "status": "skipped_409"}) is False   # upsert, not dup
    assert len(r.enrollments()) == 1
    assert r.enrollments()[0]["status"] == "skipped_409"
    assert r.accounts_enrolled() == {"a1"}
    assert r.enrolled_for("a1", "111") == {"c1"}
    assert r.enrolled_for("a1", "222") == set()
    with pytest.raises(ValueError):
        r.add_enrollment({**row, "status": "nope"})
    # sequences mapping
    r.upsert_sequence("payer", campaign_id="9", campaign_name="Payer Seq")
    r.upsert_sequence("payer", campaign_id=None)            # clear
    seq = {s["sequence_key"]: s for s in r.sequences()}
    assert seq["payer"]["campaign_id"] is None
    assert r.delete_all() == 1
    assert r.enrollments() == [] and r.sequences() == []
