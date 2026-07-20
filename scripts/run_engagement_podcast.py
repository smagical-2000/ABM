"""Daily podcast engagement pull — the published Podcast Lead Status CSV (read-only).

The sheet lives on the Work Google account, which the deployed cron can't read via
Claude's connector. So it's published to web as CSV (File -> Share -> Publish to web
-> CSV) and we fetch that URL each run — no Google auth, GET only. ICP Yes/Maybe
leads cross to scored/ABM accounts and roll into the same heat tiers (4 pts each).

Idempotent by design — events keyed by `podcast:podcast_lead:<email>`, so a daily
re-pull upserts the same rows and never duplicates; new/changed Yes/Maybe leads flow
in. No-ops cleanly if PODCAST_CSV_URL isn't set, so it's harmless until configured.

This is a leg of the daily cron (run_daily.py). Needs PODCAST_CSV_URL + DATABASE_URL.

Run:
    PODCAST_CSV_URL='https://docs.google.com/.../pub?output=csv' python scripts/run_engagement_podcast.py
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from auto_search.db import get_repository
from auto_search.db.engagement_repository import get_engagement_repository
from auto_search.db.scoring_repository import get_scoring_repository
from auto_search.engagement import sync as engagement_sync

load_dotenv()   # no override: operator env (e.g. DATABASE_URL) must win
logger = logging.getLogger("run_engagement_podcast")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    url = os.getenv("PODCAST_CSV_URL")
    if not url:
        print("[run_engagement_podcast] PODCAST_CSV_URL not set — skipping", flush=True)
        return 0

    engagement_repo = get_engagement_repository()
    engagement_repo.ensure_schema()
    try:
        stats = engagement_sync.run_podcast_url_sync(
            engagement_repo=engagement_repo,
            scoring_repo=get_scoring_repository(),
            discovery_repo=get_repository(),
            url=url)
    except Exception:  # noqa: BLE001 — cron leg: log + signal failure, don't traceback-crash
        logger.exception("[run_engagement_podcast] podcast sync failed")
        return 1
    print(f"[run_engagement_podcast] ok: {stats}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
