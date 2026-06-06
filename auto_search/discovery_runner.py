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

from auto_search import job_qualifier, pipeline
from auto_search.connectors.acquisitions import AcquisitionsConnector
from auto_search.connectors.funding import FundingConnector
from auto_search.connectors.job_postings import JobPostingsConnector
from auto_search.connectors.leadership_changes import LeadershipChangesConnector

logger = logging.getLogger(__name__)

# Sources that need no browser — safe to run in the web process.
BROWSERLESS_SOURCES = ("leadership", "acquisitions", "funding", "jobs")


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
        rows = limit if limit is not None else int(os.getenv("DISCOVERY_JOBS_MAX_ROWS", "200"))
        return JobPostingsConnector(max_rows=rows)
    raise ValueError(f"unsupported on-demand source: {name}")


async def run_once(repo, *, days: int = 1, sources=None, limit=None, on_cost=None) -> dict:
    """Run the browserless sources for the last `days` into `repo`, deduped.

    Returns a per-source summary. `on_cost(evaluated)` lets the caller record the
    qualify spend. Resilient: one source failing does not stop the others.
    """
    since = datetime.now(UTC) - timedelta(days=days)
    selected = [s for s in (sources or BROWSERLESS_SOURCES) if s in BROWSERLESS_SOURCES]
    totals = {"qualified": 0, "needs_review": 0, "disqualified": 0,
              "by_source": {}, "ran": 0, "since": since.isoformat()}

    for name in selected:
        counts = {"qualified": 0, "needs_review": 0, "disqualified": 0}
        run_id = _start_run(repo, name)
        err: str | None = None
        try:
            connector = _connector(name, limit)
            prefilter = job_qualifier.filter_job_signals if name == "jobs" else None
            async for cand in pipeline.run(
                connector, since, limit=limit,
                skip_already_qualified=repo.already_qualified, prefilter=prefilter,
                on_plan=lambda n, rid=run_id: _update_run(repo, rid, planned=n),
            ):
                repo.save_candidate(cand)
                status = cand.qualification.to_status()
                counts[status] = counts.get(status, 0) + 1
                _update_run(repo, run_id, new_companies=sum(counts.values()),
                            companies_qualified=counts["qualified"])
            totals["ran"] += 1
        except Exception as e:  # noqa: BLE001 — one source must not kill the run
            err = f"{type(e).__name__}: {e}"
            logger.exception("on-demand discovery source %s failed", name)
        finally:
            _finish_run(repo, run_id, "failed" if err else "success", err)
        totals["by_source"][name] = {**counts, "error": err}
        for k in ("qualified", "needs_review", "disqualified"):
            totals[k] += counts.get(k, 0)

    evaluated = totals["qualified"] + totals["needs_review"] + totals["disqualified"]
    if on_cost is not None and evaluated:
        try:
            on_cost(evaluated)
        except Exception:  # noqa: BLE001 — cost accounting must not break the run
            logger.exception("discovery cost hook failed")
    logger.info("on-demand discovery: %d sources ran, %d qualified, %d evaluated",
                totals["ran"], totals["qualified"], evaluated)
    return totals


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
