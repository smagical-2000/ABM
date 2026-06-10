"""Self-cleaning lifecycle sweep — Watch -> Needs review -> auto-reject (lifecycle.py)."""

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


def _row(key: str, *, icp: str = "qualified", review: str = "pending", signals=None) -> dict:
    return {"normalized_name": key, "icp_status": icp, "review_status": review,
            "signals": signals or []}


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
        return r

    def set_review(self, key, status, *, reason=None):
        r = self.rows.get(key)
        if not r:
            return None
        r["review_status"] = status
        r["rejection_reason"] = reason
        return r


# ── Watch -> Needs review ──────────────────────────────────────────────


def test_stale_watch_lead_demotes_to_needs_review():
    repo = FakeRepo([_row("biller", signals=[_job("Medical Biller", days=20)])])
    res = lifecycle.sweep(repo, now=NOW)
    assert res.demoted == 1 and res.demoted_keys == ["biller"]
    assert repo.rows["biller"]["icp_status"] == "needs_review"


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


# ── Needs review -> auto-reject ────────────────────────────────────────


def test_stale_needs_review_lead_auto_rejected():
    repo = FakeRepo([_row("old", icp="needs_review", signals=[_job("Biller", days=20)])])
    res = lifecycle.sweep(repo, now=NOW)
    assert res.rejected == 1 and repo.rows["old"]["review_status"] == "rejected"
    assert "aged out" in repo.rows["old"]["rejection_reason"]


def test_needs_review_within_grace_is_kept():
    # 10 days old: past the 7-day Watch TTL but inside the +7-day review grace
    repo = FakeRepo([_row("grace", icp="needs_review", signals=[_job("Biller", days=10)])])
    assert lifecycle.sweep(repo, now=NOW).rejected == 0


def test_reheated_needs_review_lead_is_kept():
    # a fresh exec signal reheats it to Hot — never auto-reject an in-market lead
    repo = FakeRepo([_row("reheat", icp="needs_review",
                          signals=[_job("Biller", days=20), _exec(days=1)])])
    res = lifecycle.sweep(repo, now=NOW)
    assert res.rejected == 0 and repo.rows["reheat"]["review_status"] == "pending"


def test_ttls_are_env_tunable(monkeypatch):
    monkeypatch.setenv("DISCOVERY_WATCH_TTL_DAYS", "3")
    # a 5-day-old single biller now exceeds the shortened 3-day Watch TTL
    repo = FakeRepo([_row("b", signals=[_job("Biller", days=5)])])
    assert lifecycle.sweep(repo, now=NOW).demoted == 1
