"""Self-cleaning lifecycle sweep — Watch -> Needs review -> promote-back / auto-reject.

Two clocks: Watch->review keys off SIGNAL age; review->reject keys off TIME IN
REVIEW (entered_review_at). A re-heated (Hot) review lead is promoted back to
qualified, not rejected. Intent is ABM-aware when an abm_index is passed.
"""

from datetime import UTC, datetime, timedelta

from auto_search import lifecycle

NOW = datetime(2026, 6, 10, tzinfo=UTC)


def _old(days: int) -> str:
    return (NOW - timedelta(days=days)).isoformat()


def _job(title: str, *, tier: str = "standard", days: int) -> dict:
    return {"signal_type": "job_posting", "observed_at": _old(days),
            "payload": {"job_title": title, "role": "RCM", "tier": tier}}


def _exec(*, days: int) -> dict:
    return {"signal_type": "leadership_change", "observed_at": _old(days), "payload": {}}


def _layoff(*, days: int) -> dict:
    return {"signal_type": "layoff", "observed_at": _old(days), "payload": {}}


def _row(key: str, *, icp: str = "qualified", review: str = "pending",
         signals=None, entered: str | None = None, origin: str | None = None) -> dict:
    row = {"normalized_name": key, "icp_status": icp, "review_status": review,
           "signals": signals or []}
    if entered is not None:
        row["entered_review_at"] = entered
    if origin is not None:
        row["review_origin"] = origin
    return row


class FakeRepo:
    def __init__(self, rows):
        self.rows = {r["normalized_name"]: r for r in rows}

    def panel(self, statuses=("qualified",)):
        return [r for r in self.rows.values() if r.get("icp_status") in statuses]

    def enter_needs_review(self, key):
        r = self.rows.get(key)
        if not r or r.get("icp_status") != "qualified":
            return None
        r["icp_status"] = "needs_review"
        r["entered_review_at"] = NOW.isoformat()
        r["review_origin"] = "decayed"
        return r

    def promote_from_review(self, key):
        r = self.rows.get(key)
        if not r or r.get("icp_status") != "needs_review":
            return None
        r["icp_status"] = "qualified"
        r["entered_review_at"] = None
        r["review_origin"] = None
        return r

    def set_review(self, key, status, *, reason=None):
        r = self.rows.get(key)
        if not r:
            return None
        r["review_status"] = status
        r["rejection_reason"] = reason
        return r


# ── Watch -> Needs review (signal-age clock) ───────────────────────────


def test_stale_watch_lead_demotes_and_starts_review_clock():
    repo = FakeRepo([_row("biller", signals=[_job("Medical Biller", days=20)])])
    res = lifecycle.sweep(repo, now=NOW)
    assert res.demoted == 1 and res.demoted_keys == ["biller"]
    row = repo.rows["biller"]
    assert row["icp_status"] == "needs_review"
    # demotion starts the review clock + records why it landed in review
    assert row["entered_review_at"] and row["review_origin"] == "decayed"


def test_fresh_watch_lead_is_not_demoted():
    repo = FakeRepo([_row("fresh", signals=[_job("Medical Biller", days=2)])])
    assert lifecycle.sweep(repo, now=NOW).demoted == 0


def test_hot_lead_is_never_demoted():
    # a new exec scores Hot on its base alone, even when the signal is old
    repo = FakeRepo([_row("hot", signals=[_exec(days=20)])])
    res = lifecycle.sweep(repo, now=NOW)
    assert res.demoted == 0 and repo.rows["hot"]["icp_status"] == "qualified"


def test_promoted_lead_is_never_touched():
    repo = FakeRepo([_row("promo", review="promoted", signals=[_job("Biller", days=30)])])
    res = lifecycle.sweep(repo, now=NOW)
    assert res.demoted == 0 and repo.rows["promo"]["icp_status"] == "qualified"


# ── Needs review -> auto-reject (time-in-review clock) ─────────────────


def test_long_in_review_lead_auto_rejected():
    repo = FakeRepo([_row("old", icp="needs_review", entered=_old(20),
                          signals=[_job("Biller", days=20)])])
    res = lifecycle.sweep(repo, now=NOW)
    assert res.rejected == 1 and repo.rows["old"]["review_status"] == "rejected"
    assert "aged out" in repo.rows["old"]["rejection_reason"]


def test_recently_entered_review_is_kept():
    # entered review 3 days ago — inside the 7-day review TTL
    repo = FakeRepo([_row("grace", icp="needs_review", entered=_old(3),
                          signals=[_job("Biller", days=3)])])
    assert lifecycle.sweep(repo, now=NOW).rejected == 0


def test_reject_clock_ignores_a_fresh_signal():
    # THE fix: a fresh signal no longer keeps an unactioned lead in review forever
    # — it's the time IN REVIEW that ages it out, not signal recency.
    repo = FakeRepo([_row("stuck", icp="needs_review", entered=_old(10),
                          signals=[_job("Biller", days=1)])])  # fresh but still Watch-tier
    assert lifecycle.sweep(repo, now=NOW).rejected == 1


def test_needs_review_without_a_clock_is_never_rejected():
    # safety: no entered_review_at -> we don't reject blind (the prod backfill stamps these)
    repo = FakeRepo([_row("noclock", icp="needs_review", signals=[_job("Biller", days=30)])])
    assert lifecycle.sweep(repo, now=NOW).rejected == 0


# ── Needs review -> promote back (re-heated to Hot) ────────────────────


def test_reheated_decayed_lead_is_promoted_back():
    # 'decayed' = was qualified then cooled; re-heating to Hot legitimately returns it
    repo = FakeRepo([_row("reheat", icp="needs_review", entered=_old(20), origin="decayed",
                          signals=[_job("Biller", days=20), _exec(days=1)])])
    res = lifecycle.sweep(repo, now=NOW)
    assert res.promoted == 1 and res.promoted_keys == ["reheat"]
    row = repo.rows["reheat"]
    assert row["icp_status"] == "qualified" and row["entered_review_at"] is None
    assert row["review_status"] == "pending"   # rejoins Discovery, human decision intact


def test_reheated_ingest_lead_is_NOT_promoted_stays_for_human():
    # THE guardrail: an 'ingest' lead is in review because the AI couldn't qualify it.
    # Hot intent doesn't resolve that — it must NOT be auto-promoted or auto-scored;
    # it stays in review (surfaced as Hot) for a human to judge fit.
    repo = FakeRepo([_row("unsure", icp="needs_review", entered=_old(20), origin="ingest",
                          signals=[_job("Biller", days=20), _exec(days=1)])])
    res = lifecycle.sweep(repo, now=NOW)
    assert res.promoted == 0 and res.rejected == 0
    assert repo.rows["unsure"]["icp_status"] == "needs_review"   # left for review


def test_hot_decayed_lead_promoted_not_rejected_even_when_long_in_review():
    # Hot wins over the reject timer: an in-market (and once-qualified) lead returns.
    repo = FakeRepo([_row("hotold", icp="needs_review", entered=_old(99), origin="decayed",
                          signals=[_exec(days=1)])])
    res = lifecycle.sweep(repo, now=NOW)
    assert res.promoted == 1 and res.rejected == 0


def test_hot_ingest_lead_is_never_auto_rejected():
    # Even long-in-review, a Hot 'ingest' lead is kept (flagged Hot) for review,
    # never silently aged out.
    repo = FakeRepo([_row("hotunsure", icp="needs_review", entered=_old(99), origin="ingest",
                          signals=[_exec(days=1)])])
    res = lifecycle.sweep(repo, now=NOW)
    assert res.promoted == 0 and res.rejected == 0
    assert repo.rows["hotunsure"]["icp_status"] == "needs_review"


# ── ABM consistency: the sweep applies the same bonus the panel does ──


def test_sweep_without_abm_rejects_borderline_target():
    # a lone layoff is Watch (55) without the ABM bonus -> aged out of review
    repo = FakeRepo([_row("acme", icp="needs_review", entered=_old(20),
                          signals=[_layoff(days=1)])])
    assert lifecycle.sweep(repo, now=NOW).rejected == 1


def test_sweep_with_abm_promotes_borderline_target(monkeypatch):
    # same lead (a once-qualified 'decayed' target), now confirmed ABM: +20 -> Hot
    # (75) -> promoted, not rejected. ABM bonus is what flips the sweep's verdict.
    monkeypatch.setattr(lifecycle, "_abm_confirmed", lambda _idx, _row: True)
    repo = FakeRepo([_row("acme", icp="needs_review", entered=_old(20), origin="decayed",
                          signals=[_layoff(days=1)])])
    res = lifecycle.sweep(repo, now=NOW, abm_index=object())
    assert res.promoted == 1 and res.rejected == 0
    assert repo.rows["acme"]["icp_status"] == "qualified"


# ── env-tunable TTLs ───────────────────────────────────────────────────


def test_watch_ttl_is_env_tunable(monkeypatch):
    monkeypatch.setenv("DISCOVERY_WATCH_TTL_DAYS", "3")
    repo = FakeRepo([_row("b", signals=[_job("Biller", days=5)])])
    assert lifecycle.sweep(repo, now=NOW).demoted == 1


def test_review_ttl_is_env_tunable(monkeypatch):
    monkeypatch.setenv("DISCOVERY_REVIEW_TTL_DAYS", "3")
    # entered 5 days ago: kept under the default 7, rejected under a 3-day review TTL
    repo = FakeRepo([_row("r", icp="needs_review", entered=_old(5),
                          signals=[_job("Biller", days=1)])])
    assert lifecycle.sweep(repo, now=NOW).rejected == 1


# ── next_transition: the panel TTL badge (mirrors the sweep cutoffs) ──────────


def test_next_transition_hot_is_safe():
    # Hot never decays — no countdown, whatever the status.
    assert lifecycle.next_transition(icp_status="needs_review", tier="hot",
                                     entered_review_at=_old(99), now=NOW) == (None, None)


def test_next_transition_watch_counts_down_to_review():
    # Watch lead, freshest signal 2d old, WATCH_TTL 7 → drops to review in 5d.
    last = NOW - timedelta(days=2)
    assert lifecycle.next_transition(icp_status="qualified", tier="watch",
                                     last_signal_at=last, now=NOW) == ("review", 5)


def test_next_transition_review_counts_down_to_reject():
    # In review 2d, REVIEW_TTL 7 → auto-rejects in 5d.
    assert lifecycle.next_transition(icp_status="needs_review", tier="watch",
                                     entered_review_at=_old(2), now=NOW) == ("reject", 5)


def test_next_transition_overdue_is_zero_not_negative():
    # Sat in review past the TTL → 0 ("today"), never negative.
    assert lifecycle.next_transition(icp_status="needs_review", tier="watch",
                                     entered_review_at=_old(10), now=NOW) == ("reject", 0)


def test_next_transition_none_without_a_clock():
    # Qualified but no signal yet, and review with no entry stamp → nothing to show.
    assert lifecycle.next_transition(icp_status="qualified", tier="watch",
                                     last_signal_at=None, now=NOW) == (None, None)
    assert lifecycle.next_transition(icp_status="needs_review", tier="watch",
                                     entered_review_at=None, now=NOW) == (None, None)
