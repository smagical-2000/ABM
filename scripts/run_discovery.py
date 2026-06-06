"""Unified discovery runner — all signal sources → qualify → persist.

For each connector (layoffs, leadership, M&A):
    pull signals  →  dedup by company  →  qualify via Claude (website ICP)  →
    persist verdict + provenance to the repository.

Only companies the qualifier marks `qualified` reach the review panel; every
evaluated company is still stored as the "don't re-qualify" ledger, so a
company already decided in a prior run is skipped (no repeat Claude cost).

This is the entry point a cron will call (one `since = now − interval` per run).

COST — two independent meters:
  • SignalBase  : ~per record pulled  (control with --limit)
  • Claude      : ~$0.10–0.15 per UNIQUE company qualified
Use --no-qualify for a free-ish dry run (pull + dedup + store as pending, no
Claude). Use --limit to cap records per connector.

Run:
    # cheap dry run — what would we discover? (no Claude)
    python scripts/run_discovery.py --days 7 --limit 5 --no-qualify

    # real run — discover + qualify + persist (costs Claude per company)
    python scripts/run_discovery.py --days 7 --limit 5

    # just one source
    python scripts/run_discovery.py --only leadership --days 7 --limit 5

    # show the current panel (no fetching, no cost)
    python scripts/run_discovery.py --panel
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from auto_search import job_qualifier, pipeline
from auto_search.connectors.acquisitions import AcquisitionsConnector
from auto_search.connectors.funding import FundingConnector
from auto_search.connectors.job_postings import JobPostingsConnector
from auto_search.connectors.leadership_changes import LeadershipChangesConnector
from auto_search.connectors.warntracker import WarnTrackerConnector
from auto_search.db import get_repository
from auto_search.scoring import spend_guard

load_dotenv(override=True)

BOLD, GREEN, YELLOW, RED, DIM, CYAN, RESET = (
    "\033[1m", "\033[92m", "\033[93m", "\033[91m", "\033[2m", "\033[96m", "\033[0m",
)

# Connector registry — add a new source here and it joins the run for free.
# `limit` is the per-source cost knob (records/page for SignalBase, rows/title
# for Indeed). limit=None means "no artificial cap" — page deeply for a full
# window pull (the daily cron), tunable via env so cost stays controllable.
def _sb_kwargs(limit):
    if limit is None:
        return {
            "max_pages": int(os.getenv("DISCOVERY_SIGNALBASE_MAX_PAGES", "50")),
            "per_page": int(os.getenv("DISCOVERY_SIGNALBASE_PER_PAGE", "100")),
        }
    return {"max_pages": 1, "per_page": limit}


def _jobs_rows(limit):
    return limit if limit is not None else int(os.getenv("DISCOVERY_JOBS_MAX_ROWS", "200"))


CONNECTORS = {
    "layoffs": lambda limit: WarnTrackerConnector(),
    "leadership": lambda limit: LeadershipChangesConnector(**_sb_kwargs(limit)),
    "acquisitions": lambda limit: AcquisitionsConnector(**_sb_kwargs(limit)),
    "funding": lambda limit: FundingConnector(**_sb_kwargs(limit)),
    "jobs": lambda limit: JobPostingsConnector(max_rows=_jobs_rows(limit)),
}


def configure_logging(debug: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="  %(levelname)-7s %(message)s",
    )
    for noisy in ("httpx", "httpcore", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def banner(text: str) -> None:
    print(f"\n{BOLD}{'─'*72}\n  {text}\n{'─'*72}{RESET}")


def verdict_icon(status: str) -> str:
    return {
        "qualified": f"{GREEN}✅ qualified  {RESET}",
        "needs_review": f"{YELLOW}🟡 review     {RESET}",
        "disqualified": f"{RED}❌ disqualified{RESET}",
        "error": f"{RED}⚠️  error     {RESET}",
    }.get(status, status)


async def run_connector(
    name: str,
    connector,
    since: datetime,
    repo,
    *,
    limit: int | None,
    qualify: bool,
    prefilter=None,
    spend_op=None,
) -> dict[str, int]:
    """Run one connector through the pipeline and persist results.

    `prefilter` (e.g. the job-level qualifier) runs over pulled signals before
    grouping. It costs Claude, so it's only applied on real (qualifying) runs.
    """
    banner(f"{name.upper()}  ({connector.source_name})")
    counts = {"qualified": 0, "needs_review": 0, "disqualified": 0, "error": 0}

    if not qualify:
        # Dry run: pull + dedup only, no Claude. Show what we'd evaluate.
        groups = await pipeline.collect_unique_companies(connector, since, limit=limit)
        for i, signals in enumerate(groups.values(), 1):
            rep = max(signals, key=lambda s: s.signal_strength)
            print(f"  {i:>3}  {DIM}would qualify{RESET}  {rep.company_name_raw[:40]:40}"
                  f"  ({len(signals)} signal{'s' if len(signals) != 1 else ''})")
        print(f"\n  {len(groups)} unique companies discovered (not qualified — dry run)")
        return counts

    # Real run: dedup → qualify → persist. Skip companies already decided.
    # Heartbeat a connector_runs row so the UI shows a live "processing" marker
    # and rows appear to stream in as they qualify.
    run_id = _start_run(repo, connector.source_name)
    evaluated = 0
    err: str | None = None
    try:
        async for cand in pipeline.run(
            connector, since, limit=limit,
            skip_already_qualified=repo.already_qualified,
            prefilter=prefilter,
            on_plan=lambda n: _update_run(repo, run_id, planned=n),
        ):
            repo.save_candidate(cand)
            if spend_op is not None:
                spend_guard.record_company_qualify(spend_op, cand)
            evaluated += 1
            status = cand.qualification.to_status()
            counts[status] = counts.get(status, 0) + 1
            _update_run(repo, run_id, new_companies=evaluated,
                        companies_qualified=counts["qualified"])
            q = cand.qualification
            print(f"  {verdict_icon(status)}  {BOLD}{cand.company_name[:38]:38}{RESET}"
                  f"  seg={q.segment or '—':<13} conf={q.confidence:.2f}")
            if status == "qualified" and q.reasoning:
                print(f"     {DIM}{q.reasoning[:100]}{RESET}")
    except Exception as e:  # noqa: BLE001 — mark the run failed, then re-raise
        err = f"{type(e).__name__}: {e}"
        raise
    finally:
        _finish_run(repo, run_id, "failed" if err else "success", err)
    return counts


# ── run-heartbeat helpers (no-op if the repo doesn't track runs) ───────

def _start_run(repo, source: str):
    fn = getattr(repo, "start_run", None)
    try:
        return fn(source) if fn else None
    except Exception as e:  # noqa: BLE001 — telemetry must never break a run
        logging.getLogger(__name__).debug("start_run failed: %s", e)
        return None


def _update_run(repo, run_id, **counts: int) -> None:
    if run_id is None:
        return
    fn = getattr(repo, "update_run", None)
    if fn:
        try:
            fn(run_id, **counts)
        except Exception as e:  # noqa: BLE001
            logging.getLogger(__name__).debug("update_run failed: %s", e)


def _finish_run(repo, run_id, status: str, error: str | None = None) -> None:
    if run_id is None:
        return
    fn = getattr(repo, "finish_run", None)
    if fn:
        try:
            fn(run_id, status=status, error=error)
        except Exception as e:  # noqa: BLE001
            logging.getLogger(__name__).debug("finish_run failed: %s", e)


def show_panel(repo) -> None:
    banner("REVIEW PANEL — qualified companies")
    rows = repo.panel(statuses=("qualified",))
    if not rows:
        print(f"  {DIM}empty — run discovery first{RESET}")
        return
    for i, r in enumerate(rows, 1):
        print(f"  {i:>3}  {GREEN}●{RESET} {BOLD}{r['display_name'][:38]:38}{RESET}"
              f"  seg={r.get('segment') or '—':<13} conf={r.get('confidence', 0):.2f}"
              f"  signals={len(r.get('signals', []))}")
        if r.get("evidence_url"):
            print(f"       {DIM}{r['evidence_url']}{RESET}")
    print(f"\n  {len(rows)} qualified compan{'y' if len(rows) == 1 else 'ies'} in panel")


async def main(args: argparse.Namespace) -> int:
    """Returns a process exit code: 0 ok (incl. partial-source failures),
    1 if every selected source failed (a total failure the scheduler should
    flag), 2 on a usage error."""
    configure_logging(args.debug)
    repo = get_repository()

    if args.panel:
        show_panel(repo)
        return 0

    since = datetime.now(UTC) - timedelta(days=args.days)
    # --no-limit (or --limit 0) removes the artificial per-source cap: the daily
    # cron pulls the full window and pages deeply (env-tunable).
    limit = None if (args.no_limit or args.limit == 0) else args.limit
    if args.sources:
        selected = [s.strip() for s in args.sources.split(",") if s.strip()]
    elif args.only:
        selected = [args.only]
    else:
        selected = list(CONNECTORS)
    unknown = [s for s in selected if s not in CONNECTORS]
    if unknown:
        print(f"{RED}unknown source(s): {unknown}. valid: {list(CONNECTORS)}{RESET}")
        return 2

    print(f"\n{BOLD}Discovery run{RESET}  since {since.date()} ({args.days}d), "
          f"limit {'no cap' if limit is None else f'{limit}/source'}, sources={selected}, "
          f"{'QUALIFY' if not args.no_qualify else 'dry run (no Claude)'}")
    if not args.no_qualify:
        print(f"  {YELLOW}Cost: SignalBase per record + ~$0.10–0.15 Claude "
              f"per new company{RESET}")

    totals: dict[str, int] = {}
    ran = failed = 0
    spend_op = None
    if not args.no_qualify:
        try:
            from auto_search.db.scoring_repository import get_scoring_repository
            scoring_repo = get_scoring_repository()
            if hasattr(scoring_repo, "ensure_schema"):
                scoring_repo.ensure_schema()
            spend_op = spend_guard.Operation(
                scoring_repo, "discovery_cron",
                estimated_usd=0.0, accounts_planned=0,
            )
        except Exception:  # noqa: BLE001 — cost tracking must not break discovery
            logging.getLogger(__name__).exception("discovery spend op init failed")

    for name in selected:
        try:
            connector = CONNECTORS[name](limit)
            # The jobs source gets the cheap job-level qualifier as a pre-filter
            # (title + JD) so only genuine RCM postings reach company scoring.
            # Record the prefilter's (paid) spend on the same op so the meter is
            # accurate, not just the per-company website qualification.
            def _on_prefilter_spend(spend, _op=spend_op):
                if _op is not None:
                    _op.record(step="qualify", actual_usd=spend.cost_usd,
                               model=spend.model,
                               metadata={"input_tokens": spend.input_tokens,
                                         "output_tokens": spend.output_tokens,
                                         "measured": True, "phase": "job_prefilter"})

            prefilter = None
            if name == "jobs" and not args.no_job_filter:
                def prefilter(sigs, _s=_on_prefilter_spend):
                    return job_qualifier.filter_job_signals(sigs, on_spend=_s)
            counts = await run_connector(
                name, connector, since, repo,
                limit=limit, qualify=not args.no_qualify,
                prefilter=prefilter, spend_op=spend_op,
            )
        except Exception as e:  # noqa: BLE001 — one source must not kill the cron
            failed += 1
            logging.getLogger(__name__).error(
                "connector %s failed: %s", name, e, exc_info=args.debug)
            print(f"  {RED}⚠️  {name} failed: {type(e).__name__}: {e}{RESET}")
            continue
        ran += 1
        for k, v in counts.items():
            totals[k] = totals.get(k, 0) + v

    if not args.no_qualify:
        banner("RUN SUMMARY")
        print(f"  {GREEN}qualified:     {totals.get('qualified', 0)}{RESET}")
        print(f"  {YELLOW}needs review:  {totals.get('needs_review', 0)}{RESET}")
        print(f"  {RED}disqualified:  {totals.get('disqualified', 0)}{RESET}")
        print(f"  {RED}errors:        {totals.get('error', 0)}{RESET}")
        print(f"\n  store totals: {repo.stats()}")
        print(f"  {DIM}panel (qualified) → python scripts/run_discovery.py --panel{RESET}")
        if spend_op is not None:
            spend_op.finish(status="completed")

    # Production exit code: a single source failing keeps exit 0 (resilient), but
    # a TOTAL failure (every selected source errored) exits non-zero so the
    # scheduler marks the run failed and can alert.
    if selected and ran == 0:
        print(f"\n  {RED}all {failed} source(s) failed — exiting non-zero{RESET}")
        return 1
    if failed:
        print(f"  {YELLOW}{failed} of {len(selected)} source(s) failed (others ran){RESET}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--days", type=int, default=7,
                   help="Lookback window per source (default 7)")
    p.add_argument("--limit", type=int, default=5,
                   help="Max records/companies per source (cost knob, default 5). "
                        "Use 0 (or --no-limit) for no cap.")
    p.add_argument("--no-limit", action="store_true",
                   help="No artificial per-source cap — pull the full window and "
                        "page deeply (the daily cron mode). Env-tunable: "
                        "DISCOVERY_SIGNALBASE_MAX_PAGES/PER_PAGE, DISCOVERY_JOBS_MAX_ROWS.")
    p.add_argument("--only", choices=list(CONNECTORS),
                   help="Run a single source")
    p.add_argument("--sources",
                   help="Comma-separated sources to run (e.g. "
                        "'leadership,acquisitions,funding'). Default: all.")
    p.add_argument("--no-qualify", action="store_true",
                   help="Dry run: discover + dedup only, no Claude qualification")
    p.add_argument("--no-job-filter", action="store_true",
                   help="Skip the job-level qualifier for the jobs source "
                        "(send every RCM-titled posting straight to company scoring)")
    p.add_argument("--panel", action="store_true",
                   help="Show the current qualified-company panel and exit")
    p.add_argument("--debug", action="store_true")
    sys.exit(asyncio.run(main(p.parse_args())))
