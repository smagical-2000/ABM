"""End-to-end test of the Layoffs.fyi auto-search pipeline.

No DB writes. Just prints results so you can eyeball quality before
committing to the full pipeline build.

Setup:
    1. Download CSV from https://layoffs.fyi/ (Download button)
    2. Save it as ./data/layoffs.csv
    3. Add to .env:
        ANTHROPIC_API_KEY=sk-ant-...        (after rotating!)
        LAYOFFS_CSV_PATH=./data/layoffs.csv

Run:
    python scripts/test_layoffs_pipeline.py
    python scripts/test_layoffs_pipeline.py --since 2026-01-01 --limit 20
    python scripts/test_layoffs_pipeline.py --rules-only       # no LLM cost
    python scripts/test_layoffs_pipeline.py --no-qualify       # connector only

What to look at:
    - Are the companies that pass rules actually healthcare-ICP?
    - Are the LLM verdicts agreeing with your gut?
    - How many fall into needs_human_review?
    - How many disqualifications were obvious (saves Galyna time)?
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Make 'auto_search' importable when running this from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from auto_search.connectors.layoffs_fyi import LayoffsFyiConnector
from auto_search.qualifier import passes_rules, qualify
from dotenv import load_dotenv

load_dotenv()


# ─── pretty printing ─────────────────────────────────────────────────

BOLD = "\033[1m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
DIM = "\033[2m"
RESET = "\033[0m"


def banner(text: str, color: str = BOLD) -> None:
    print(f"\n{color}{'─' * 70}\n  {text}\n{'─' * 70}{RESET}")


def status_icon(qualified: bool, needs_review: bool) -> str:
    if needs_review:
        return f"{YELLOW}🟡 REVIEW{RESET}"
    if qualified:
        return f"{GREEN}✅ QUALIFIED{RESET}"
    return f"{RED}❌ DISQUAL{RESET}"


# ─── main ────────────────────────────────────────────────────────────

async def main(args: argparse.Namespace) -> None:
    # default: last 30 days
    if args.since:
        since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
    else:
        since = datetime.now(timezone.utc) - timedelta(days=args.days)

    banner("Layoffs.fyi → Auto Search Pipeline Test")
    print(f"  CSV source:  {os.getenv('LAYOFFS_CSV_PATH') or 'URL'}")
    print(f"  Window:      since {since.date()}")
    print(f"  Limit:       {args.limit or 'no cap'}")
    print(f"  Qualify:     {'disabled' if args.no_qualify else 'rules + LLM'}")

    connector = LayoffsFyiConnector()
    stats = {
        "fetched": 0, "passed_rules": 0,
        "qualified": 0, "needs_review": 0, "disqualified": 0,
    }
    qualified_rows: list[dict] = []

    banner("Healthcare signals after connector pre-filter")
    header = f"{BOLD}  {'#':>3}  {'COMPANY':30}  {'LAID OFF':>8}  {'STRENGTH':>8}  INDUSTRY{RESET}"
    print(header)

    async for signal in connector.pull(since=since):
        stats["fetched"] += 1

        # quick line print
        idx = stats["fetched"]
        industry = (signal.payload.get("industry_raw") or "")[:25]
        laid = signal.payload.get("laid_off_count") or "?"
        print(f"  {idx:>3}  {signal.company_name_raw[:30]:30}  "
              f"{str(laid):>8}  {signal.signal_strength:>8.2f}  {DIM}{industry}{RESET}")

        if args.limit and stats["fetched"] >= args.limit:
            break

    # qualification pass
    if args.no_qualify:
        banner("Stopping before qualification (per --no-qualify)")
        _print_stats(stats)
        return

    banner("Running qualification (rules first, LLM on the survivors)")
    # Re-pull (small dataset — fine for test)
    count = 0
    async for signal in connector.pull(since=since):
        count += 1
        if args.limit and count > args.limit:
            break

        ok, reason = passes_rules(signal)
        if not ok:
            stats["disqualified"] += 1
            if args.verbose:
                print(f"  {RED}rules-killed{RESET}  {signal.company_name_raw}  "
                      f"{DIM}({reason}){RESET}")
            continue
        stats["passed_rules"] += 1

        if args.rules_only:
            stats["qualified"] += 1
            print(f"  {status_icon(True, False)}  {signal.company_name_raw}")
            continue

        # Stage 2 LLM
        result = await qualify(signal)
        if result.needs_human_review:
            stats["needs_review"] += 1
        elif result.qualified:
            stats["qualified"] += 1
            qualified_rows.append({
                "company": signal.company_name_raw,
                "segment": result.segment,
                "sub_segment": result.sub_segment,
                "confidence": result.confidence,
                "reasoning": result.reasoning,
                "laid_off": signal.payload.get("laid_off_count"),
                "industry": signal.payload.get("industry_raw"),
            })
        else:
            stats["disqualified"] += 1

        icon = status_icon(result.qualified, result.needs_human_review)
        seg = (result.segment or "—")[:18]
        sub = (result.sub_segment or "—")[:18]
        print(f"  {icon}  {signal.company_name_raw[:28]:28}  "
              f"{seg:18}  {sub:18}  conf={result.confidence:.2f}")
        if args.verbose:
            print(f"     {DIM}{result.reasoning}{RESET}")

    banner("Summary")
    _print_stats(stats)

    if qualified_rows:
        banner("Qualified candidates (would enter pending_companies)")
        for r in qualified_rows:
            print(f"  {GREEN}●{RESET} {r['company']:30}  "
                  f"{r['segment']}/{r['sub_segment']}  "
                  f"conf={r['confidence']:.2f}  "
                  f"laid_off={r['laid_off']}")
            print(f"     {DIM}{r['reasoning']}{RESET}")

        # dump to JSON for sharing
        out_path = Path("data/test_qualified_layoffs.json")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(qualified_rows, indent=2, default=str))
        print(f"\n  → wrote {len(qualified_rows)} qualified rows to {out_path}")


def _print_stats(stats: dict) -> None:
    print(f"  fetched (post-pre-filter):  {stats['fetched']}")
    print(f"  passed rules:                {stats['passed_rules']}")
    print(f"  {GREEN}qualified:                   {stats['qualified']}{RESET}")
    print(f"  {YELLOW}needs human review:          {stats['needs_review']}{RESET}")
    print(f"  {RED}disqualified:                {stats['disqualified']}{RESET}")


# ─── CLI ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--since", help="ISO date (e.g. 2026-01-01)")
    p.add_argument("--days", type=int, default=90,
                   help="If --since not given, look back this many days (default 90)")
    p.add_argument("--limit", type=int, help="Max rows to process")
    p.add_argument("--no-qualify", action="store_true",
                   help="Skip qualification — just print connector output")
    p.add_argument("--rules-only", action="store_true",
                   help="Run rules pre-filter only, skip LLM (free)")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Print disqualification reasons + LLM reasoning")
    args = p.parse_args()

    asyncio.run(main(args))
