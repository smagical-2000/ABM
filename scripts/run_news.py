"""Daily market-intelligence news refresh — the News tab feeds itself.

Same engine as the UI's Refresh button (auto_search/news/runner.run_once):
Google News RSS per topic query -> filter to NEW urls -> enrich (topic +
why-it-matters, paid, tiny) -> store. Idempotent (dedup by URL); only new
articles are enriched, so a daily run costs cents. Before this leg the feed
refreshed only when someone pressed the button (~3 refreshes all July).

Cost is recorded under the SAME op_type ("news_refresh") as the manual
button, so the SpendMeter sees one meter either way. The run summary is
stamped to `ops_news_last_run` for the daily digest's source lines.

Run:
    python scripts/run_news.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from auto_search.db import get_repository
from auto_search.db.scoring_repository import get_scoring_repository
from auto_search.news import runner
from auto_search.scoring import spend_guard

load_dotenv()   # no override: operator env (e.g. DATABASE_URL) must win
logger = logging.getLogger("run_news")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    repo = get_repository()
    if hasattr(repo, "ensure_schema"):
        repo.ensure_schema()
    op = spend_guard.Operation(get_scoring_repository(), "news_refresh",
                               estimated_usd=0.0, accounts_planned=0)

    def on_cost(usd: float) -> None:
        op.record(step="news_enrich", actual_usd=usd, model="news")

    try:
        summary = asyncio.run(runner.run_once(repo, on_cost=on_cost))
    except Exception:  # noqa: BLE001 — cron leg: log + signal failure, don't crash
        logger.exception("[run_news] failed")
        return 1
    finally:
        op.finish()
    _stamp(summary)
    print(f"[run_news] ok: {summary}", flush=True)
    return 0


def _stamp(summary: dict) -> None:
    """Best-effort digest stamp — the digest's news line reads this. Never fatal."""
    try:
        from auto_search.db.engagement_repository import get_engagement_repository
        erepo = get_engagement_repository()
        erepo.set_setting("ops_news_last_run", json.dumps(
            {**summary, "at": datetime.now(UTC).isoformat()}))
    except Exception:  # noqa: BLE001 — stamping must never fail the run
        logger.exception("[run_news] digest stamp failed")


if __name__ == "__main__":
    # os._exit via run_entrypoint, not sys.exit: psycopg pool threads can hang
    # interpreter finalization forever (the Jul 24-27 cron freeze class).
    from auto_search.ops.shutdown import run_entrypoint
    run_entrypoint(main)
