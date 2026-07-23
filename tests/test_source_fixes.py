"""MAR2-45 source fixes (2026-07-23 live audit) — regression tests.

Five silent-death modes, one test file:
  (a) the SignalBase "today" preset only covered 00:00→cron-time UTC (~37% of
      the week at 12:31Z) — the floor is last_7d now, for all three connectors;
  (b) healthcare.py evaluated subcategory-include BEFORE industry-exclude, so
      VitalRads (veterinary industry, "healthcare" subcategory) passed;
  (c) warntracker served a frozen April sample for weeks of "successful" runs —
      a stale feed must now RAISE, not quietly yield nothing;
  (d) no source could ever page us for producing nothing — the zero-streak
      check must breach on silence and post ONE consolidated alert;
  (e) the leadership connector must narrow via `seniorities` (the free-text
      `positions` feed collapsed ~Jul 1 — 0 rows since).

No live API calls anywhere: fakes, fixtures, and tmp caches only.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from auto_search.clients.signalbase import JobChangeRecord
from auto_search.connectors import acquisitions, funding, leadership_changes
from auto_search.connectors.warntracker import WarnTrackerConnector
from auto_search.healthcare import is_healthcare_provider
from auto_search.ops import source_streaks

NOW = datetime.now(UTC)


# ── (a) date-preset floor: never "today" ─────────────────────────────


class TestPresetFloor:
    def test_one_day_since_maps_to_last_7d(self):
        since = NOW - timedelta(days=1)
        for mod in (acquisitions, funding, leadership_changes):
            assert mod._since_to_preset(since) == "last_7d", mod.__name__

    def test_today_is_never_returned(self):
        # Sweep the whole plausible range — "today" must be unreachable.
        for days in (0, 1, 2, 6, 7, 8, 14, 29, 59, 89, 179, 400):
            since = NOW - timedelta(days=days)
            for mod in (acquisitions, funding, leadership_changes):
                assert mod._since_to_preset(since) != "today", (mod.__name__, days)

    def test_wider_windows_unchanged(self):
        assert funding._since_to_preset(NOW - timedelta(days=20)) == "last_30d"
        assert acquisitions._since_to_preset(NOW - timedelta(days=80)) == "last_90d"


# ── (b) healthcare gate ordering: exclude before include ─────────────


class TestHealthcareOrdering:
    def test_excluded_industry_beats_included_subcategory(self):
        # VitalRads (2026-07-23 audit): industry "Veterinary Services" +
        # SignalBase subcategory "healthcare" slipped through because the
        # subcategory-include ran first. The industry disqualifier must win.
        assert not is_healthcare_provider("Veterinary Services", "healthcare")
        assert not is_healthcare_provider("Pharmaceutical Manufacturing", "healthcare")

    def test_reverse_included_industry_and_subcategory_passes(self):
        assert is_healthcare_provider("Hospitals and Health Care", "healthcare")

    def test_excluded_subcategory_still_beats_included_industry(self):
        # Curevo regression — must survive the reorder.
        assert not is_healthcare_provider("Hospitals and Health Care", "biotechnology")

    def test_included_subcategory_with_no_industry_still_passes(self):
        # Empty industry can't be "excluded" — the pre-reorder behavior where
        # a bare "healthcare" subcategory qualifies must be preserved.
        assert is_healthcare_provider(None, "healthcare")
        assert is_healthcare_provider("", "insurance")


# ── (c) warntracker freshness tripwire ───────────────────────────────


def _warn_row(notice: datetime, company: str = "Acme Health") -> dict:
    return {
        "Company Name": company,
        "# Laid off": 150,
        "Layoff date": notice.date().isoformat(),
        "Notice Date": notice.date().isoformat(),
        "State": "OH",
        "📍 City/Jurisdiction": "Toledo",
        "companyId": company.lower().replace(" ", "-"),
    }


def _cached_connector(tmp_path, monkeypatch, rows: list[dict]) -> WarnTrackerConnector:
    cache = tmp_path / "warn_cache.json"
    cache.write_text(json.dumps(rows))
    monkeypatch.setenv("WARN_USE_CACHE", "true")
    monkeypatch.setenv("WARN_CACHE_PATH", str(cache))
    return WarnTrackerConnector()


class TestWarntrackerStaleTripwire:
    async def test_stale_feed_raises(self, tmp_path, monkeypatch):
        # Every notice 60d old = the frozen-sample failure mode (Jun–Jul 2026:
        # the endpoint served an April snapshot and runs stayed green).
        rows = [_warn_row(NOW - timedelta(days=60)),
                _warn_row(NOW - timedelta(days=75), company="Beta Care")]
        c = _cached_connector(tmp_path, monkeypatch, rows)
        with pytest.raises(RuntimeError, match="frozen sample"):
            async for _ in c.pull(since=NOW - timedelta(days=365)):
                pass

    async def test_fresh_feed_passes_and_yields(self, tmp_path, monkeypatch):
        rows = [_warn_row(NOW - timedelta(days=2))]
        c = _cached_connector(tmp_path, monkeypatch, rows)
        out = [s async for s in c.pull(since=NOW - timedelta(days=30))]
        assert len(out) == 1
        assert out[0].company_name_raw == "Acme Health"

    async def test_empty_feed_does_not_trip(self, tmp_path, monkeypatch):
        # No rows = a different failure (fetch error, already logged) — the
        # tripwire is specifically for a POPULATED-but-frozen payload.
        c = _cached_connector(tmp_path, monkeypatch, [])
        assert [s async for s in c.pull(since=NOW - timedelta(days=30))] == []


# ── (d) zero-streak check + ONE consolidated alert ───────────────────


class _FakeStateRepo:
    """get_setting/set_setting — the alerts.should_alert throttle contract."""

    def __init__(self):
        self._kv: dict[str, str] = {}

    def get_setting(self, key):
        return self._kv.get(key)

    def set_setting(self, key, value):
        self._kv[key] = value


def _fake_discovery_repo(last_by_source: dict[str, int]) -> object:
    """A JsonFileRepository-shaped fake: `_store` rows whose FIRST signal names
    the producing source, first_seen_at = days-ago per the map."""

    class _Repo:
        pass

    repo = _Repo()
    repo._store = {
        f"co_{src}": {
            "first_seen_at": (NOW - timedelta(days=days)).isoformat(),
            "signals": [{"source": src, "source_external_id": f"{src}::1"}],
        }
        for src, days in last_by_source.items()
    }
    return repo


class TestZeroStreak:
    def _repo_all_fresh_except(self, stale: dict[str, int]):
        ages = {src: 1 for src in source_streaks.THRESHOLDS}
        ages.update(stale)
        return _fake_discovery_repo(ages)

    def test_breach_listing(self):
        repo = self._repo_all_fresh_except({"signalbase_funding": 12})
        streaks = source_streaks.compute_streaks(repo, now=NOW)
        by_src = {s["source"]: s for s in streaks}
        assert by_src["signalbase_funding"]["breached"]        # 12d >= 10d
        assert by_src["signalbase_funding"]["days_silent"] == 12
        assert not by_src["jobs"]["breached"]                  # 1d < 3wd
        assert not by_src["warntracker"]["breached"]           # 1d < 10d

    def test_never_produced_is_a_breach(self):
        repo = self._repo_all_fresh_except({})
        del repo._store["co_social_event"]
        streaks = source_streaks.compute_streaks(repo, now=NOW)
        row = next(s for s in streaks if s["source"] == "social_event")
        assert row["breached"] and row["days_silent"] is None

    def test_single_consolidated_alert_with_both_names(self, monkeypatch):
        repo = self._repo_all_fresh_except(
            {"signalbase_funding": 12, "warntracker": 40})
        calls: list[dict] = []
        monkeypatch.setattr(source_streaks, "post_ops_alert",
                            lambda **kw: calls.append(kw) or True)
        state = _FakeStateRepo()

        source_streaks.check_streaks(repo, alert=True, state_repo=state, now=NOW)

        assert len(calls) == 1                       # ONE alert for BOTH breaches
        kw = calls[0]
        assert kw["kind"] == "source-silence"
        assert kw["severity"] == "warning"
        assert "signalbase_funding" in kw["detail"]
        assert "warntracker" in kw["detail"]
        assert "jobs:" not in kw["detail"]           # fresh source not named
        assert "MAR2-45" in kw["runbook"]            # runbook names the audit doc

        # Second run inside the 24h gap: throttled, no second post.
        source_streaks.check_streaks(repo, alert=True, state_repo=state, now=NOW)
        assert len(calls) == 1

    def test_all_fresh_posts_nothing(self, monkeypatch):
        repo = self._repo_all_fresh_except({})
        calls: list[dict] = []
        monkeypatch.setattr(source_streaks, "post_ops_alert",
                            lambda **kw: calls.append(kw) or True)
        source_streaks.check_streaks(repo, alert=True,
                                     state_repo=_FakeStateRepo(), now=NOW)
        assert calls == []


# ── (e) leadership server-side params: seniorities, not positions ────


class _CapturingClient:
    def __init__(self):
        self.kwargs: dict | None = None

    async def iter_job_changes(self, **kwargs):
        self.kwargs = kwargs
        rec = JobChangeRecord(
            signalId="sig-1",
            occurredAt=(NOW - timedelta(days=2)).isoformat(),
            personName="Jane Doe",
            newRole="Chief Financial Officer",
            companyName="Acme Health System",
            companyIndustry="Hospitals and Health Care",
            companyCountry="US",
        )
        yield rec


class TestLeadershipServerParams:
    async def test_seniorities_sent_positions_absent(self):
        fake = _CapturingClient()
        connector = leadership_changes.LeadershipChangesConnector(client=fake)
        out = [s async for s in connector.pull(since=NOW - timedelta(days=14))]
        assert len(out) == 1                        # sanity: the pull still yields
        assert fake.kwargs["seniorities"] == "c_level,vp,director"
        assert "positions" not in fake.kwargs       # dead feed no longer requested
        assert fake.kwargs["date_preset"] != "today"
