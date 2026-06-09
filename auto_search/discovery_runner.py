"""On-demand discovery run, triggered from the panel.

Lets the team pull the last 24h of signals manually — not only via the scheduled
cron. It runs in the web process, which already lives inside Railway with prod-DB
access, so results land straight in the live panel, deduped.

Scope: the BROWSERLESS sources only (leadership, acquisitions, funding, jobs).
The web image carries the Apify client + Claude but intentionally NOT Chromium,
so layoffs (WARN, which needs a headless browser) stays with the cron worker —
keeping a heavy scrape out of the API process.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta

from auto_search import job_qualifier, job_stacking, pipeline
from auto_search.connectors.acquisitions import AcquisitionsConnector
from auto_search.connectors.funding import FundingConnector
from auto_search.connectors.job_postings import JobPostingsConnector
from auto_search.connectors.leadership_changes import LeadershipChangesConnector

logger = logging.getLogger(__name__)

# Sources that need no browser — safe to run in the web process.
BROWSERLESS_SOURCES = ("leadership", "acquisitions", "funding", "jobs")

# Jobs pull over a wider "currently-open" window than the other sources: RCM
# reqs stay open for weeks, and signal-stacking needs a company's co-open roles
# to land in the SAME run to be counted together. The window only changes
# recency, NOT how many rows are scraped (Apify bills per row, capped per
# title), so widening it is ~free. Other sources keep the run's `days` window.
JOBS_WINDOW_DAYS = max(1, int(os.getenv("DISCOVERY_JOBS_WINDOW_DAYS", "14")))


def _sb_kwargs(limit):
    if limit is None:
        return {
            "max_pages": int(os.getenv("DISCOVERY_SIGNALBASE_MAX_PAGES", "50")),
            "per_page": int(os.getenv("DISCOVERY_SIGNALBASE_PER_PAGE", "100")),
        }
    return {"max_pages": 1, "per_page": limit}


def _connector(name: str, limit):
    if name == "leadership":
        return LeadershipChangesConnector(**_sb_kwargs(limit))
    if name == "acquisitions":
        return AcquisitionsConnector(**_sb_kwargs(limit))
    if name == "funding":
        return FundingConnector(**_sb_kwargs(limit))
    if name == "jobs":
        # Per-title-per-board row cap = the scrape-cost knob. With 24 titles the
        # old default of 200 (→ clamped 50) was a huge credit sink; the wide
        # window gives recency without volume, so a small cap is plenty. Pulling
        # the most-recent dozen per title is enough to surface a company that's
        # actively stacking RCM reqs. Env-overridable for a deeper sweep.
        rows = limit if limit is not None else int(os.getenv("DISCOVERY_JOBS_MAX_ROWS", "12"))
        return JobPostingsConnector(max_rows=rows)
    raise ValueError(f"unsupported on-demand source: {name}")


async def run_once(repo, *, days: int = 1, sources=None, limit=None,
                   on_company=None, gate=None, on_prefilter_spend=None) -> dict:
    """Run the browserless sources for the last `days` into `repo`, deduped.

    Returns a per-source summary. `on_company(candidate)` is called after each
    company is saved so the caller can record per-company qualify spend.
    `gate` is an optional async checkpoint (pause/cancel) awaited before each
    company and before each new source. `on_prefilter_spend(LlmSpend)` records
    the job-qualifier prefilter cost. Resilient: one source failing does not
    stop the others.
    """
    since = datetime.now(UTC) - timedelta(days=days)
    selected = [s for s in (sources or BROWSERLESS_SOURCES) if s in BROWSERLESS_SOURCES]
    totals = {"qualified": 0, "needs_review": 0, "disqualified": 0, "parked": 0,
              "by_source": {}, "ran": 0, "since": since.isoformat(),
              "cancelled": False}

    for name in selected:
        # Don't even start a new source if the run was cancelled (or wait here
        # while paused). gate() returning False == cancelled.
        if gate is not None and not await gate():
            totals["cancelled"] = True
            logger.info("discovery run cancelled before source %s", name)
            break
        counts = {"qualified": 0, "needs_review": 0, "disqualified": 0}
        parked = {"n": 0}                       # mutable box for the on_defer closure
        run_id = _start_run(repo, name)
        err: str | None = None
        try:
            connector = _connector(name, limit)
            src_since = since
            prefilter = defer = on_defer = None
            if name == "jobs":
                # Bind the gate + spend hook into the jobs prefilter so
                # pause/cancel reach it and its (paid) cost is recorded.
                def prefilter(sigs, _g=gate, _s=on_prefilter_spend):
                    return job_qualifier.filter_job_signals(sigs, gate=_g, on_spend=_s)

                # Wider window so co-open RCM reqs stack within one run, and the
                # per-company stacking gate that parks single low-tier postings.
                src_since = datetime.now(UTC) - timedelta(days=max(days, JOBS_WINDOW_DAYS))

                def defer(_key, sigs):
                    return job_stacking.should_park(sigs)

                def on_defer(key, sigs, _p=parked):
                    _p["n"] += 1
                    _park(repo, key, sigs)
            async for cand in pipeline.run(
                connector, src_since, limit=limit,
                skip_already_qualified=repo.already_qualified, prefilter=prefilter,
                defer=defer, on_defer=on_defer,
                on_plan=lambda n, rid=run_id: _update_run(repo, rid, planned=n),
                gate=gate,
            ):
                repo.save_candidate(cand)
                status = cand.qualification.to_status()
                counts[status] = counts.get(status, 0) + 1
                if on_company is not None:
                    try:
                        on_company(cand)
                    except Exception:  # noqa: BLE001 — cost hook must not break a run
                        logger.exception("on_company hook failed for %s", cand.company_key)
                _update_run(repo, run_id, new_companies=sum(counts.values()),
                            companies_qualified=counts["qualified"])
            totals["ran"] += 1
        except Exception as e:  # noqa: BLE001 — one source must not kill the run
            err = f"{type(e).__name__}: {e}"
            logger.exception("on-demand discovery source %s failed", name)
        finally:
            _finish_run(repo, run_id, "failed" if err else "success", err)
        totals["by_source"][name] = {**counts, "parked": parked["n"], "error": err}
        for k in ("qualified", "needs_review", "disqualified"):
            totals[k] += counts.get(k, 0)
        totals["parked"] += parked["n"]

    evaluated = totals["qualified"] + totals["needs_review"] + totals["disqualified"]
    totals["evaluated"] = evaluated
    logger.info("on-demand discovery: %d sources ran, %d qualified, %d evaluated, "
                "%d parked", totals["ran"], totals["qualified"], evaluated,
                totals["parked"])
    return totals


# ── stacking watch ledger (no-op if the repo doesn't support parking) ─────


def _park(repo, company_key: str, signals) -> None:
    """Persist a deferred (stacking-parked) company to the watch ledger.

    Guarded like the heartbeat helpers: a repo (or test double) without
    `upsert_parked` simply doesn't track the watch — parking still works as a
    pure skip, you just don't get the UI watch list.
    """
    fn = getattr(repo, "upsert_parked", None)
    if not fn:
        return
    try:
        fn(job_stacking.watch_record(
            company_key, signals, job_stacking.stacking_decision(signals)))
    except Exception as e:  # noqa: BLE001 — watch ledger must never break a run
        logger.debug("upsert_parked failed for %s: %s", company_key, e)


# ── connector_runs heartbeat (no-op if the repo doesn't track runs) ───────


def _start_run(repo, source: str):
    fn = getattr(repo, "start_run", None)
    try:
        return fn(source) if fn else None
    except Exception as e:  # noqa: BLE001 — telemetry must never break a run
        logger.debug("start_run failed: %s", e)
        return None


def _update_run(repo, run_id, **counts: int) -> None:
    fn = getattr(repo, "update_run", None) if run_id is not None else None
    if fn:
        try:
            fn(run_id, **counts)
        except Exception as e:  # noqa: BLE001
            logger.debug("update_run failed: %s", e)


def _finish_run(repo, run_id, status: str, error: str | None = None) -> None:
    fn = getattr(repo, "finish_run", None) if run_id is not None else None
    if fn:
        try:
            fn(run_id, status=status, error=error)
        except Exception as e:  # noqa: BLE001
            logger.debug("finish_run failed: %s", e)
