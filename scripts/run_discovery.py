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

load_dotenv(override=True)

BOLD, GREEN, YELLOW, RED, DIM, CYAN, RESET = (
    "\033[1m", "\033[92m", "\033[93m", "\033[91m", "\033[2m", "\033[96m", "\033[0m",
)

# Connector registry — add a new source here and it joins the run for free.
# `limit` is the per-source cost knob: records/page for SignalBase, rows/title
# for Indeed.
CONNECTORS = {
    "layoffs": lambda limit: WarnTrackerConnector(),
    "leadership": lambda limit: LeadershipChangesConnector(max_pages=1, per_page=limit),
    "acquisitions": lambda limit: AcquisitionsConnector(max_pages=1, per_page=limit),
    "funding": lambda limit: FundingConnector(max_pages=1, per_page=limit),
    "jobs": lambda limit: JobPostingsConnector(max_rows=limit),
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
    limit: int,
    qualify: bool,
    prefilter=None,
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
        ):
            repo.save_candidate(cand)
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


async def main(args: argparse.Namespace) -> None:
    configure_logging(args.debug)
    repo = get_repository()

    if args.panel:
        show_panel(repo)
        return

    since = datetime.now(UTC) - timedelta(days=args.days)
    if args.sources:
        selected = [s.strip() for s in args.sources.split(",") if s.strip()]
    elif args.only:
        selected = [args.only]
    else:
        selected = list(CONNECTORS)
    unknown = [s for s in selected if s not in CONNECTORS]
    if unknown:
        print(f"{RED}unknown source(s): {unknown}. valid: {list(CONNECTORS)}{RESET}")
        return

    print(f"\n{BOLD}Discovery run{RESET}  since {since.date()} ({args.days}d), "
          f"limit {args.limit}/source, sources={selected}, "
          f"{'QUALIFY' if not args.no_qualify else 'dry run (no Claude)'}")
    if not args.no_qualify:
        print(f"  {YELLOW}Cost: SignalBase per record + ~$0.10–0.15 Claude "
              f"per new company{RESET}")

    totals: dict[str, int] = {}
    for name in selected:
        try:
            connector = CONNECTORS[name](args.limit)
            # The jobs source gets the cheap job-level qualifier as a pre-filter
            # (title + JD) so only genuine RCM postings reach company scoring.
            prefilter = (
                job_qualifier.filter_job_signals
                if name == "jobs" and not args.no_job_filter
                else None
            )
            counts = await run_connector(
                name, connector, since, repo,
                limit=args.limit, qualify=not args.no_qualify,
                prefilter=prefilter,
            )
        except Exception as e:  # noqa: BLE001 — one source must not kill the cron
            logging.getLogger(__name__).error(
                "connector %s failed: %s", name, e, exc_info=args.debug)
            print(f"  {RED}⚠️  {name} failed: {type(e).__name__}: {e}{RESET}")
            continue
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


if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--days", type=int, default=7,
                   help="Lookback window per source (default 7)")
    p.add_argument("--limit", type=int, default=5,
                   help="Max records/companies per source (cost knob, default 5)")
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
    asyncio.run(main(p.parse_args()))
