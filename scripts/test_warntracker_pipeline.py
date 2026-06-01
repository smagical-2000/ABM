"""End-to-end test of the WARN-tracker discovery pipeline.

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
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from auto_search import pipeline
from auto_search.connectors.warntracker import WarnTrackerConnector

load_dotenv()

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
        datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
        if args.since
        else datetime.now(timezone.utc) - timedelta(days=args.days)
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
        for i, (key, signals) in enumerate(groups.items(), 1):
            rep = max(signals, key=lambda s: s.signal_strength)
            print(f"  {i:>3}  {rep.company_name_raw[:38]:38}"
                  f"  signals={len(signals)}  s={rep.signal_strength:.2f}")
        print(f"\n  {len(groups)} unique companies "
              f"(from {sum(len(v) for v in groups.values())} signals)")
        return

    # ── full pipeline ──
    banner("Qualifying each unique company via Claude + web_search")
    stats = {"qualified": 0, "needs_review": 0, "disqualified": 0}
    qualified_rows: list[dict] = []

    idx = 0
    async for cand in pipeline.run(connector, since, limit=limit):
        idx += 1
        q = cand.qualification

        if q.needs_human_review:
            stats["needs_review"] += 1
        elif q.qualified:
            stats["qualified"] += 1
            qualified_rows.append({
                "company": cand.company_name,
                "segment": q.segment,
                "sub_segment": q.sub_segment,
                "company_type": q.company_type,
                "employees": q.approximate_employees,
                "confidence": q.confidence,
                "reasoning": q.reasoning,
                "evidence_url": q.evidence_url,
                "signal_count": len(cand.signals),
                "laid_off": cand.primary_signal.payload.get("laid_off_count"),
                "state": cand.primary_signal.payload.get("state"),
            })
        else:
            stats["disqualified"] += 1

        print(f"\n  {idx:>3}  {cand.company_name}")
        print(f"       {verdict_icon(q.qualified, q.needs_human_review)}  "
              f"seg={q.segment or '—':<14} sub={q.sub_segment or '—':<16} "
              f"type={q.company_type:<9} conf={q.confidence:.2f} "
              f"signals={len(cand.signals)}")
        if q.evidence_url:
            print(f"       {DIM}↳ {q.evidence_url}{RESET}")
        if args.verbose or q.qualified:
            print(f"       {DIM}{q.reasoning}{RESET}")

    banner("Summary")
    print(f"  Unique companies:    {idx}")
    print(f"  {GREEN}qualified:           {stats['qualified']}{RESET}")
    print(f"  {YELLOW}needs review:        {stats['needs_review']}{RESET}")
    print(f"  {RED}disqualified:        {stats['disqualified']}{RESET}")

    if qualified_rows:
        out = Path("data/test_qualified_warn.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(qualified_rows, indent=2, default=str))
        print(f"\n  → wrote {len(qualified_rows)} qualified companies to {out}")


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
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Print reasoning for every company, not just qualified")
    p.add_argument("--debug", action="store_true", help="DEBUG logging")
    asyncio.run(main(p.parse_args()))
