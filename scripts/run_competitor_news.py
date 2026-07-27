"""Daily competitor distress scan — negative press on monitored competitors.

Reads the competitor list from social_targets (kind='competitor') and pulls
Google News RSS (free) for each name near distress terms, storing hits as
news_items under a "Competitor: <name>" topic with the fast-follower play.
Idempotent (dedup by URL). An entry point the daily Railway cron calls.

Run:
    python scripts/run_competitor_news.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from auto_search.db import get_repository
from auto_search.news import competitors

load_dotenv()   # no override: operator env (e.g. DATABASE_URL) must win
logger = logging.getLogger("run_competitor_news")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    repo = get_repository()
    if hasattr(repo, "ensure_schema"):
        repo.ensure_schema()
    try:
        summary = asyncio.run(competitors.run_competitor_news(repo))
    except Exception:  # noqa: BLE001 — cron leg: log + signal failure, don't crash
        logger.exception("[run_competitor_news] failed")
        return 1
    print(f"[run_competitor_news] ok: {summary}", flush=True)
    return 0


if __name__ == "__main__":
    # os._exit via run_entrypoint, not sys.exit: psycopg pool threads can hang
    # interpreter finalization forever (the Jul 24-27 cron freeze class).
    from auto_search.ops.shutdown import run_entrypoint
    run_entrypoint(main)
