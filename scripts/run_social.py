"""Daily social-engagement poll — scrape monitored LinkedIn accounts via Apify.

For every active monitored account (Magical's own + competitors):
    scrape recent posts + engagers (Apify)  →  decision-maker filter (free)  →
    enrich survivors (Apify, paid+capped)  →  qualify the company (Claude)  →
    persist to the discovery panel.

This is the entry point the daily Railway cron calls. Only decision-makers from
ICP-fit companies reach the panel; the cost-shaping (filter before enrich, cap
enrichments, skip already-qualified companies) lives in poll_targets/ingest.

COST — two meters:
  • Apify  : ~$0.002/engager scraped + $0.008/decision-maker enriched
  • Claude : ~$0.10-0.15 per UNIQUE new company qualified
Use --since-hours to bound the window (default 24) and --max-enrich to cap paid
enrichments per run.

Run:
    python scripts/run_social.py --since-hours 24 --max-enrich 100
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

from auto_search.db import get_repository
from auto_search.db.scoring_repository import get_scoring_repository
from auto_search.scoring import spend_guard
from auto_search.social import SocialTarget, poll_events, poll_targets

load_dotenv(override=True)
logger = logging.getLogger("run_social")


async def _main(args) -> int:
    repo = get_repository()
    scoring_repo = get_scoring_repository()
    active = [SocialTarget(**t) for t in repo.social_targets() if t.get("active", True)]
    keywords = [k["keyword"] for k in repo.event_keywords()
                if k.get("active", True) and k.get("keyword")]
    if not active and not keywords:
        logger.warning("no active monitored accounts or event keywords — nothing to poll")
        return 0

    # The ONE shared budget gate (same as the webhook + on-demand run).
    gate, cap, est, blocked_now = spend_guard.make_social_gate(scoring_repo)
    if blocked_now:
        logger.warning("monthly discovery budget reached — skipping social poll")
        return 0

    since = (datetime.now(UTC) - timedelta(hours=args.since_hours)).isoformat()
    # Never enrich more than we can qualify (enrich is paid; qualifying is capped).
    max_enrich = min(args.max_enrich, cap)
    op = spend_guard.Operation(scoring_repo, "social_cron", estimated_usd=round(cap * est, 4))
    try:
        if active:
            logger.info("social poll: %s", await poll_targets(
                active, repo=repo, op=op, can_qualify=gate,
                max_posts=args.max_posts, posted_limit_date=since, max_enrich=max_enrich))
        if keywords:
            logger.info("event poll: %s", await poll_events(
                keywords, repo=repo, op=op, can_qualify=gate,
                date_filter="past-24h", max_enrich=max_enrich))
    finally:
        op.finish()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--since-hours", type=int, default=24, help="look-back window")
    p.add_argument("--max-posts", type=int, default=10, help="posts per account")
    p.add_argument("--max-enrich", type=int, default=100, help="cap paid enrichments/run")
    p.add_argument("--debug", action="store_true")
    args = p.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
    try:
        return asyncio.run(_main(args))
    except Exception:  # noqa: BLE001 — cron must exit non-zero on failure, with a trace
        logger.exception("social poll failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
