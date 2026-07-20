"""Daily Reply.io engagement pull (read-only).

Reply.io was the FIRST engagement source but never had a daily leg — its heat
silently froze whenever nobody ran the sync by hand (last manual run 2026-07-14,
found 2026-07-20 with six days of stale reply/click heat). This is the missing
leg: pull, cross, store. Idempotent — the sync upserts by external_id, so an
overlapping re-pull never duplicates.

No REPLYIO_API_KEY → clean no-op (like the podcast/notify legs): the daily run
must not go red on a service that never had the key configured.

Window: by default it resumes from the last recorded sync minus a 10-day
overlap (late-arriving activity lands inside the overlap); a store that has
never synced pulls the full 2026 cohort. `--since` overrides either.

Run:
    python scripts/run_engagement_replyio.py                      # windowed
    python scripts/run_engagement_replyio.py --since 2026-01-01   # full re-pull
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from auto_search.db import get_repository
from auto_search.db.engagement_repository import get_engagement_repository
from auto_search.db.scoring_repository import get_scoring_repository
from auto_search.engagement import sync as engagement_sync

load_dotenv()   # no override: operator env (e.g. DATABASE_URL) must win
logger = logging.getLogger("run_engagement_replyio")


def _default_since(engagement_repo) -> str:
    """Window start when --since is not given: last sync minus a 10-day overlap
    (idempotent upserts absorb the re-pull; late activity lands inside it),
    else the 2026 cohort start for a store that has never synced."""
    try:
        state = engagement_repo.get_sync_state("replyio") or {}
        last = state.get("last_synced_at")
        if last:
            dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            return (dt.date() - timedelta(days=10)).isoformat()
    except (ValueError, TypeError):
        logger.warning("unparseable replyio last_synced_at — using full window")
    return "2026-01-01"


def main() -> int:
    ap = argparse.ArgumentParser(description="Daily Reply.io engagement pull")
    ap.add_argument("--since", default=None,
                    help="pull window start (YYYY-MM-DD; default: last sync − 10 days)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if not os.getenv("REPLYIO_API_KEY"):
        print("[run_engagement_replyio] REPLYIO_API_KEY not set — skip. No-op.", flush=True)
        return 0
    engagement_repo = get_engagement_repository()
    since = args.since or _default_since(engagement_repo)
    try:
        stats = asyncio.run(engagement_sync.run_sync(
            engagement_repo=engagement_repo,
            scoring_repo=get_scoring_repository(),
            discovery_repo=get_repository(),
            since=since))
    except Exception:
        logger.exception("replyio sync failed")
        return 1
    print(f"[run_engagement_replyio] ok (since {since}): {stats}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
