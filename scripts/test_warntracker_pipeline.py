"""DEPRECATED — single-source warntracker test harness.

The canonical entry point is now `scripts/run_discovery.py`, which runs all
connectors through dedup → qualify → persist. Keep this only for isolated
warntracker debugging (e.g. tuning the Playwright fetch with --cache).
Prefer: `python scripts/run_discovery.py --only layoffs ...`

---

End-to-end test of the WARN-tracker discovery pipeline.

Pipeline under test:
    warntracker.com (Playwright)
      ↓  structural filter (date window, ≥10 laid off)
      ↓  dedup by company  ← one Claude call per company, never repeated
      ↓  Claude + web_search visits each company's website
      ↓  structured ICP verdict
      ↓  print + save qualified companies

No DB writes — output is JSON for human review before we wire up storage.

Run:
    python scripts/test_warntracker_pipeline.py                # 10 companies
    python scripts/test_warntracker_pipeline.py --limit 5 -v   # 5, with reasoning
    python scripts/test_warntracker_pipeline.py --no-qualify   # fetch only, free
    python scripts/test_warntracker_pipeline.py --since 2026-01-01
    python scripts/test_warntracker_pipeline.py --cache        # use cached rows

Cost: ~$0.10–0.15 per unique company (Claude Sonnet 4.5 + web_search).
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

from auto_search import pipeline
from auto_search.connectors.warntracker import WarnTrackerConnector
from auto_search.db import JsonFileRepository

load_dotenv(override=True)

BOLD, GREEN, YELLOW, RED, DIM, CYAN, RESET = (
    "\033[1m", "\033[92m", "\033[93m", "\033[91m",
    "\033[2m", "\033[96m", "\033[0m",
)
DEFAULT_LIMIT = 10


def configure_logging(debug: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="  %(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("httpx", "httpcore", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def banner(text: str) -> None:
    print(f"\n{BOLD}{'─' * 72}\n  {text}\n{'─' * 72}{RESET}")


def verdict_icon(qualified: bool, needs_review: bool) -> str:
    if needs_review:
        return f"{YELLOW}🟡 REVIEW   {RESET}"
    return f"{GREEN}✅ QUALIFIED{RESET}" if qualified else f"{RED}❌ DISQUAL  {RESET}"


async def main(args: argparse.Namespace) -> None:
    configure_logging(args.debug)
    if args.cache:
        os.environ["WARN_USE_CACHE"] = "true"

    since = (
        datetime.fromisoformat(args.since).replace(tzinfo=UTC)
        if args.since
        else datetime.now(UTC) - timedelta(days=args.days)
    )
    limit = args.limit or DEFAULT_LIMIT

    banner("WARN-tracker → Discovery Pipeline Test")
    print("  Source:    warntracker.com (WARN notices)")
    print(f"  Window:    since {since.date()} ({args.days}d)")
    print(f"  Limit:     {limit} unique companies")
    print(f"  Qualify:   {'disabled' if args.no_qualify else 'Claude + web_search'}")
    if not args.no_qualify:
        print(f"  Est cost:  ~${limit * 0.10:.2f}–${limit * 0.15:.2f}")

    connector = WarnTrackerConnector()

    # ── fetch-only mode: prove the connector + dedup without spending LLM ──
    if args.no_qualify:
        banner("Fetching + deduping signals (no qualification)")
        groups = await pipeline.collect_unique_companies(
            connector, since, limit=limit
        )
        for i, (_key, signals) in enumerate(groups.items(), 1):
            rep = max(signals, key=lambda s: s.signal_strength)
            print(f"  {i:>3}  {rep.company_name_raw[:38]:38}"
                  f"  signals={len(signals)}  s={rep.signal_strength:.2f}")
        print(f"\n  {len(groups)} unique companies "
              f"(from {sum(len(v) for v in groups.values())} signals)")
        return

    # ── full pipeline (with persistence + cross-run dedup) ──
    # The repository both stores results AND tells the pipeline which
    # companies were already decided in a PRIOR run, so they're skipped
    # (no repeat Claude call) unless --no-skip is passed.
    repo = JsonFileRepository()
    skip = None if args.no_skip else repo.already_qualified

    banner("Qualifying each unique company via Claude + web_search")
    stats = {"qualified": 0, "needs_review": 0, "disqualified": 0, "error": 0}

    idx = 0
    async for cand in pipeline.run(connector, since, limit=limit,
                                   skip_already_qualified=skip):
        idx += 1
        q = cand.qualification
        repo.save_candidate(cand)          # persist verdict + signals
        stats[q.to_status()] = stats.get(q.to_status(), 0) + 1

        print(f"\n  {idx:>3}  {cand.company_name}")
        print(f"       {verdict_icon(q.qualified, q.needs_human_review)}  "
              f"status={q.to_status():<13} seg={q.segment or '—':<14} "
              f"sub={q.sub_segment or '—':<16} conf={q.confidence:.2f} "
              f"signals={len(cand.signals)}")
        if q.evidence_url:
            print(f"       {DIM}↳ {q.evidence_url}{RESET}")
        if args.verbose or q.qualified:
            print(f"       {DIM}{q.reasoning}{RESET}")

    banner("Summary")
    print(f"  Companies this run:  {idx}")
    print(f"  {GREEN}qualified:           {stats.get('qualified', 0)}{RESET}")
    print(f"  {YELLOW}needs review:        {stats.get('needs_review', 0)}{RESET}")
    print(f"  {RED}disqualified:        {stats.get('disqualified', 0)}{RESET}")
    print(f"  {RED}errors:              {stats.get('error', 0)}{RESET}")
    print("\n  Persisted to: data/discovery_store.json")
    print(f"  {DIM}Re-run to confirm already-decided companies are skipped.{RESET}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--since", help="ISO date, e.g. 2026-01-01")
    p.add_argument("--days", type=int, default=90,
                   help="Lookback window if --since not given (default 90)")
    p.add_argument("--limit", type=int,
                   help=f"Max UNIQUE companies to qualify (default {DEFAULT_LIMIT})")
    p.add_argument("--no-qualify", action="store_true",
                   help="Fetch + dedup only — no LLM, no cost")
    p.add_argument("--cache", action="store_true",
                   help="Use cached WARN rows (no browser)")
    p.add_argument("--no-skip", action="store_true",
                   help="Re-qualify companies already decided in prior runs")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Print reasoning for every company, not just qualified")
    p.add_argument("--debug", action="store_true", help="DEBUG logging")
    asyncio.run(main(p.parse_args()))
