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

from auto_search import job_qualifier, job_stacking, lifecycle, pipeline
from auto_search.clients.upstream import UpstreamQuotaError
from auto_search.connectors.acquisitions import AcquisitionsConnector
from auto_search.connectors.funding import FundingConnector
from auto_search.connectors.job_postings import JobPostingsConnector
from auto_search.connectors.leadership_changes import LeadershipChangesConnector
from auto_search.connectors.warntracker import WarnTrackerConnector
from auto_search.db import get_repository
from auto_search.ops.alerts import post_ops_alert, should_alert
from auto_search.scoring import spend_guard

load_dotenv()   # no override: operator env (e.g. DATABASE_URL) must win

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
    # PER title-query, per board (24 titles -> ~35 searches). Must match the
    # runner's default (12): the old 200 here meant a cron redeploy quietly
    # scraped ~16x the rows the panel Run does. Override via env for a deep sweep.
    return limit if limit is not None else int(os.getenv("DISCOVERY_JOBS_MAX_ROWS", "12"))


CONNECTORS = {
    "layoffs": lambda limit: WarnTrackerConnector(),
    "leadership": lambda limit: LeadershipChangesConnector(**_sb_kwargs(limit)),
    "acquisitions": lambda limit: AcquisitionsConnector(**_sb_kwargs(limit)),
    "funding": lambda limit: FundingConnector(**_sb_kwargs(limit)),
    "jobs": lambda limit: JobPostingsConnector(max_rows=_jobs_rows(limit)),
}

# RARE sources emit a handful of events per week, so the cron's --days 1 window
# is a coverage bug for them: combined with the "today" server preset it gave
# ~37% weekly coverage at the 12:31Z cron (missed TytoCare/Solaris/BioTrace/
# VitalRads purely by timestamp — 2026-07-23 audit). Every daily run now pulls
# a 14-day window for these three; re-seen records are FREE because the dedup
# ledger absorbs them (uq_discovery_signal on signalId + the company row's
# first_seen/already_qualified skip — no repeat Claude cost, no twin rows).
# Jobs/layoffs/social stay on --days: they're high-volume and daily-fresh.
RARE_SOURCE_LOOKBACK_DAYS = int(os.getenv("DISCOVERY_RARE_LOOKBACK_DAYS", "14"))
RARE_SOURCES = frozenset({"leadership", "acquisitions", "funding"})


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
    counts = {"qualified": 0, "needs_review": 0, "disqualified": 0, "error": 0, "parked": 0}

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

    # Jobs cost gate — the SAME gate the on-demand panel run uses, now on the cron
    # too: park a company whose only signal is a single STANDARD posting (store it
    # on the watch ledger, skip the paid qualify, re-evaluate next run once it
    # stacks). CORE roles, stacked roles, and any non-job signal still qualify.
    # The wide jobs pull window lands co-open reqs together, so stacking decides
    # within one run. This is what stops us paying to qualify lone low-intent hires.
    defer = on_defer = None
    if name == "jobs":
        def defer(_key, sigs):
            return job_stacking.should_park(sigs)

        def on_defer(key, sigs):
            counts["parked"] += 1
            job_stacking.persist_parked(repo, key, sigs)

    try:
        async for cand in pipeline.run(
            connector, since, limit=limit,
            skip_already_qualified=repo.already_qualified,
            prefilter=prefilter, defer=defer, on_defer=on_defer,
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


# ── connector-failure alerting ────────────────────────────────────────
# A source that RAISES must page. Before 2026-07-27 the only loud path for a
# dead source was run_digest's 24h source-silence WARNING (lumped with every
# other quiet source), because the 1-of-N policy below keeps the run at exit 0
# when other sources survive — so warntracker's stale-feed tripwire and a
# total Apify quota outage both read as "green run, quiet market".

_QUOTA_KIND = "upstream-quota"
_QUOTA_RUNBOOK = (
    "The Apify account is capped or blocked — this is billing, not code, and it "
    "is account-wide: jobs discovery, ALL SignalBase feeds, social listening and "
    "LinkedIn TOFU capture are down until it clears. Check usage/limits in the "
    "Apify console (raise the monthly hard limit or wait for the cycle reset), "
    "then re-run scripts/run_discovery.py --days 1 --no-limit.")
_FAIL_KIND = "connector-failure"
_FAIL_RUNBOOK = (
    "One or more discovery connectors raised. Per-source triage lives in the "
    "2026-07-23 live source audit (MAR2-45); a stale/frozen upstream feed needs "
    "a source swap, not a retry.")


def alert_failed_sources(failures: dict[str, str], *, state_repo=None) -> bool:
    """ONE consolidated ops alert naming every source that failed this run.

    Quota failures get their own kind + runbook (one incident, one fix, every
    source). Throttled via alerts.should_alert so a multi-day outage doesn't
    post a card per run. Best-effort by contract — alerting can never break the
    run it reports on. Returns True iff an alert was posted."""
    if not failures:
        return False
    quota = {s for s, err in failures.items() if "UpstreamQuotaError" in err}
    kind = _QUOTA_KIND if quota else _FAIL_KIND
    try:
        if state_repo is None:
            from auto_search.db.engagement_repository import (
                get_engagement_repository,
            )
            state_repo = get_engagement_repository()
    except Exception:  # noqa: BLE001 — throttle state is optional, alerting is not
        logging.getLogger(__name__).warning(
            "alert throttle state unavailable — alerting unthrottled")
        state_repo = None
    try:
        if state_repo is not None and not should_alert(state_repo, kind, min_gap_hours=6.0):
            return False
        title = (f"Upstream QUOTA exceeded — {len(failures)} discovery source(s) DEAD"
                 if quota else f"{len(failures)} discovery source(s) FAILED")
        return post_ops_alert(
            kind=kind, severity="failure", service="discovery-cron", title=title,
            detail="\n".join(f"{src}: {err[:200]}" for src, err in sorted(failures.items())),
            runbook=_QUOTA_RUNBOOK if quota else _FAIL_RUNBOOK)
    except Exception:  # noqa: BLE001 — best-effort by contract
        logging.getLogger(__name__).exception("connector-failure alert failed")
        return False


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
    # Rare-source floor: never NARROWER than --days (min() of datetimes = the
    # earlier cutoff = the wider window), so a manual --days 30 sweep still
    # sweeps 30d while the daily --days 1 cron gets its 14d rare window.
    rare_since = min(since,
                     datetime.now(UTC) - timedelta(days=RARE_SOURCE_LOOKBACK_DAYS))
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
    if rare_since < since and any(s in RARE_SOURCES for s in selected):
        print(f"  {DIM}rare-source window: {sorted(RARE_SOURCES & set(selected))} "
              f"since {rare_since.date()} ({RARE_SOURCE_LOOKBACK_DAYS}d floor — "
              f"dedup ledger absorbs repeats){RESET}")
    if not args.no_qualify:
        print(f"  {YELLOW}Cost: SignalBase per record + ~$0.10–0.15 Claude "
              f"per new company{RESET}")

    totals: dict[str, int] = {}
    failures: dict[str, str] = {}   # source -> error, for the consolidated alert
    ran = 0
    spend_op = None
    scoring_repo = None
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
                name, connector,
                rare_since if name in RARE_SOURCES else since, repo,
                limit=limit, qualify=not args.no_qualify,
                prefilter=prefilter, spend_op=spend_op,
            )
        except Exception as e:  # noqa: BLE001 — one source must not kill the cron
            failures[name] = f"{type(e).__name__}: {e}"
            logging.getLogger(__name__).error(
                "connector %s failed: %s", name, e, exc_info=args.debug)
            print(f"  {RED}⚠️  {name} failed: {type(e).__name__}: {e}{RESET}")
            if isinstance(e, UpstreamQuotaError):
                # Account-wide cap — the remaining sources share the key and
                # would each burn a timeout proving the same thing.
                print(f"  {RED}upstream account capped — skipping remaining "
                      f"source(s){RESET}")
                for skipped in selected[selected.index(name) + 1:]:
                    failures[skipped] = f"{type(e).__name__}: skipped (account capped)"
                break
            continue
        ran += 1
        for k, v in counts.items():
            totals[k] = totals.get(k, 0) + v

    if not args.no_qualify:
        banner("RUN SUMMARY")
        print(f"  {GREEN}qualified:     {totals.get('qualified', 0)}{RESET}")
        print(f"  {YELLOW}needs review:  {totals.get('needs_review', 0)}{RESET}")
        print(f"  {RED}disqualified:  {totals.get('disqualified', 0)}{RESET}")
        print(f"  {DIM}parked (watch): {totals.get('parked', 0)} — single low-tier hires, not qualified{RESET}")
        print(f"  {RED}errors:        {totals.get('error', 0)}{RESET}")
        print(f"\n  store totals: {repo.stats()}")
        print(f"  {DIM}panel (qualified) → python scripts/run_discovery.py --panel{RESET}")
        if spend_op is not None:
            spend_op.finish(status="completed")
        # Self-cleaning pass: cold Watch -> Needs review, re-heated -> promoted
        # back to Discovery, in-review-too-long -> auto-rejected. ABM-aware so the
        # sweep's Hot bar matches the panel's.
        try:
            from auto_search.abm import AbmIndex, TargetAccount
            abm_rows = repo.abm_targets() if hasattr(repo, "abm_targets") else []
            abm_index = AbmIndex([TargetAccount(**r) for r in abm_rows])
        except Exception:  # noqa: BLE001 — abm match is a bonus; never break the sweep
            abm_index = None
        sweep = lifecycle.sweep(repo, abm_index=abm_index)
        print(f"  {DIM}lifecycle: {sweep.demoted} → needs-review, "
              f"{sweep.promoted} → promoted, {sweep.rejected} auto-rejected{RESET}")
        # Auto-score the re-heated (promoted) leads, budget-permitting.
        if sweep.promoted_keys and scoring_repo is not None:
            from auto_search import autoscore
            from auto_search.scoring.service import ScoringService
            from auto_search.services.review import ReviewService
            res = await autoscore.autoscore_promoted(
                sweep.promoted_keys, review=ReviewService(repo),
                scoring=ScoringService(scoring_repo), scoring_repo=scoring_repo)
            if res["scored"]:
                print(f"  {DIM}auto-scored {len(res['scored'])} promoted lead(s){RESET}")

    # A failing source is now TOLD, not just logged — one consolidated card per
    # run (throttled), so a dead connector pages instead of waiting for the
    # digest's 24h silence warning.
    alert_failed_sources(failures)

    # Production exit code: a single source failing keeps exit 0 (resilient), but
    # a TOTAL failure (every selected source errored) exits non-zero so the
    # scheduler marks the run failed and can alert.
    if selected and ran == 0:
        print(f"\n  {RED}all {len(failures)} source(s) failed — exiting non-zero{RESET}")
        return 1
    if failures:
        print(f"  {YELLOW}{len(failures)} of {len(selected)} source(s) failed "
              f"(others ran){RESET}")
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
