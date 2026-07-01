"""Hourly LinkedIn TOFU ad-engagement run — the entry point a Railway cron calls.

Scrape reactions on Magical's sponsored posts -> ABM-only -> Apollo email -> dedup
-> upsert into the "LinkedIn <> Airtable" table + Reply.io campaign contact + record
`linkedin_tofu` heat. (SFDC creation is handled downstream by the Airtable automation.)

DISABLED BY DEFAULT. A live run is a no-op unless LINKEDIN_TOFU_CRON_ENABLED=1, so
the cron service can be created/scheduled but will not write a thing until you flip
that env var on (after confirming the manual run looks right). `--dry-run` always
runs (no writes) regardless of the flag, for testing the wiring.

Needs DATABASE_URL, APIFY_API_KEY, APOLLO_API_KEY, REPLYIO_API_KEY,
AIRTABLE_API_KEY, AIRTABLE_BASE_ID, AIRTABLE_LINKEDIN_TABLE in the env.

Run:
    python scripts/run_linkedin_tofu.py --dry-run                  # safe, no writes
    LINKEDIN_TOFU_CRON_ENABLED=1 python scripts/run_linkedin_tofu.py   # live
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from auto_search.db import get_repository
from auto_search.db.engagement_repository import get_engagement_repository
from auto_search.db.scoring_repository import get_scoring_repository
from auto_search.engagement import linkedin_ads, linkedin_ads_runner

load_dotenv(override=True)
logger = logging.getLogger("run_linkedin_tofu")

_DEFAULT_CSV = (Path(__file__).resolve().parent.parent
                / "auto_search" / "engagement" / "linkedin_tofu_shares.csv")

_SYNC_SOURCE = "linkedin_tofu"   # sync_state key for the cost-guard throttle


def _hours_since(ts) -> float | None:
    """Hours since an ISO/datetime timestamp, or None if unset/unparseable."""
    if not ts:
        return None
    try:
        dt = ts if isinstance(ts, datetime) else datetime.fromisoformat(
            str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return (datetime.now(UTC) - dt).total_seconds() / 3600
    except (ValueError, TypeError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="LinkedIn TOFU ad-engagement run")
    ap.add_argument("--dry-run", action="store_true",
                    help="no writes; ignores the enable flag (for testing)")
    ap.add_argument("--max-reactions", type=int, default=50, help="reactions per post")
    ap.add_argument("--max-contacts", type=int, default=None, help="people processed per run")
    ap.add_argument("--max-leads", type=int, default=None, help="stop after N leads")
    ap.add_argument("--csv", default=None, help="share_id,category CSV (default: packaged)")
    ap.add_argument("--force", action="store_true",
                    help="bypass the min-interval cost throttle (manual immediate run)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    # The kill switch: a live run does nothing until the env flag is explicitly on.
    if not args.dry_run and os.getenv("LINKEDIN_TOFU_CRON_ENABLED") != "1":
        print("[run_linkedin_tofu] DISABLED — set LINKEDIN_TOFU_CRON_ENABLED=1 to run "
              "live, or pass --dry-run. No-op.", flush=True)
        return 0

    engagement_repo = get_engagement_repository()
    engagement_repo.ensure_schema()

    # Cost guard: the Railway cron ticks every 15 min, but ad reactions barely change and
    # re-scraping + re-enriching every tick was ~$12/day of Apify. Do the real work only
    # every LINKEDIN_TOFU_MIN_INTERVAL_HOURS (default 6); other ticks skip BEFORE any spend.
    # --dry-run and --force bypass it.
    min_h = float(os.getenv("LINKEDIN_TOFU_MIN_INTERVAL_HOURS", "6"))
    if not (args.dry_run or args.force) and min_h > 0:
        last = (engagement_repo.get_sync_state(source=_SYNC_SOURCE) or {}).get("last_synced_at")
        hrs = _hours_since(last)
        if hrs is not None and hrs < min_h:
            print(f"[run_linkedin_tofu] throttled: last run {hrs:.1f}h ago (< {min_h}h) "
                  "— no-op. (--force to override)", flush=True)
            return 0

    csv_path = args.csv or os.getenv("LINKEDIN_TOFU_CSV") or str(_DEFAULT_CSV)
    share_categories = linkedin_ads.load_share_categories(Path(csv_path).read_text())
    if not share_categories:
        logger.error("no usable share_ids in %s", csv_path)
        return 1

    airtable = reply = None
    if not args.dry_run:
        from auto_search.engagement.airtable_client import AirtableClient
        from auto_search.engagement.replyio_client import ReplyioClient
        airtable = AirtableClient()
        reply = ReplyioClient()
    try:
        summary = asyncio.run(linkedin_ads_runner.run(
            share_categories=share_categories, engagement_repo=engagement_repo,
            scoring_repo=get_scoring_repository(), discovery_repo=get_repository(),
            airtable_client=airtable, replyio_client=reply, max_reactions=args.max_reactions,
            max_contacts=args.max_contacts, max_leads=args.max_leads, dry_run=args.dry_run))
    except Exception:  # noqa: BLE001 — cron leg: log + signal failure, don't traceback-crash
        logger.exception("[run_linkedin_tofu] run failed")
        return 1
    if not args.dry_run:                       # stamp the last real run for the throttle
        engagement_repo.set_sync_state(source=_SYNC_SOURCE, status="ok", stats=summary["stats"])
    print(f"[run_linkedin_tofu] {'dry-run ' if args.dry_run else ''}ok: {summary['stats']}",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
