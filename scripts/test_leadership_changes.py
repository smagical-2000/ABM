"""Test / demo the SignalBase leadership-changes connector.

Detects US healthcare leaders (CEO/CFO/CMO/CNO/CIO, revenue-cycle, finance,
population-health) who recently changed jobs — deterministically, from
SignalBase's real-time job-change feed (each record has an `occurredAt` date).

CREDIT NOTE: SignalBase is billed via Apify at ~1 credit per page (≤50 rows).
The connector pages newest-first and STOPS once it crosses the date cutoff, so
a daily run is typically 1 page. `--pages` caps it.

Run:
    python scripts/test_leadership_changes.py                    # 30d, 3 pages
    python scripts/test_leadership_changes.py --days 7 --pages 1 # daily-cron shape
    python scripts/test_leadership_changes.py --qualify          # + Claude ICP check
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

from auto_search import pipeline
from auto_search.connectors.leadership_changes import LeadershipChangesConnector

load_dotenv()

BOLD, GREEN, YELLOW, RED, DIM, RESET = (
    "\033[1m", "\033[92m", "\033[93m", "\033[91m", "\033[2m", "\033[0m",
)


def configure_logging(debug: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="  %(levelname)-7s %(message)s",
    )
    for noisy in ("httpx", "httpcore", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


async def main(args: argparse.Namespace) -> None:
    configure_logging(args.debug)
    since = datetime.now(UTC) - timedelta(days=args.days)

    print(f"\n{BOLD}{'─'*70}\n  SignalBase Leadership Changes — US Healthcare"
          f"\n{'─'*70}{RESET}")
    print(f"  Window:  since {since.date()} ({args.days}d)")
    print(f"  Pages:   up to {args.pages}  (~{args.pages} Apify credit(s))")

    connector = LeadershipChangesConnector(max_pages=args.pages)

    if not args.qualify:
        n = 0
        async for sig in connector.pull(since=since):
            n += 1
            p = sig.payload
            print(f"\n  {n}. {BOLD}{p['new_role']}{RESET}  @ {sig.company_name_raw}")
            print(f"     {p.get('person_name') or '—'} · {p.get('company_industry')}"
                  f" · started {str(p.get('occurred_at'))[:10]} · strength {sig.signal_strength}")
            if p.get("person_linkedin"):
                print(f"     {DIM}{p['person_linkedin']}{RESET}")
        print(f"\n  {GREEN}{n} healthcare leadership signals{RESET}\n")
        return

    print(f"\n  {DIM}Running full pipeline (SignalBase + Claude qualification)…{RESET}")
    n = 0
    async for cand in pipeline.run(connector, since, limit=args.pages * 50):
        n += 1
        q = cand.qualification
        icon = (f"{GREEN}✅{RESET}" if q.qualified else
                f"{YELLOW}🟡{RESET}" if q.needs_human_review else f"{RED}❌{RESET}")
        trig = cand.primary_signal.payload
        print(f"\n  {n}. {icon} {BOLD}{cand.company_name}{RESET}  "
              f"[{q.to_status()}] seg={q.segment or '—'} conf={q.confidence:.2f}")
        print(f"     trigger: {trig.get('new_role')} ({trig.get('person_name')})")
        if q.reasoning:
            print(f"     {DIM}{q.reasoning}{RESET}")
    print(f"\n  {n} companies evaluated\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--days", type=int, default=30,
                   help="Lookback window on occurredAt (default 30)")
    p.add_argument("--pages", type=int, default=3,
                   help="Max pages to fetch (≈ credits). Connector stops early "
                        "at the date cutoff (default 3)")
    p.add_argument("--qualify", action="store_true",
                   help="Run Claude ICP qualification on each company")
    p.add_argument("--debug", action="store_true")
    asyncio.run(main(p.parse_args()))
