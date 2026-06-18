"""Weekly engagement digest — post the accounts that heated up to Slack.

Lean by design (Galyna: reps won't read a data dump): a count + the top movers,
one reason each, a console link. Read-only over the engagement DB; the only write
is the Slack post. Safe — never enriches, never spends credits. If
SLACK_ENGAGEMENT_WEBHOOK isn't set it logs + no-ops (so it's harmless until wired).

Run:
    python scripts/run_engagement_digest.py --days 7            # post last 7 days
    python scripts/run_engagement_digest.py --dry-run           # print, don't post
    python scripts/run_engagement_digest.py --test              # mark as a test post
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from auto_search.db.engagement_repository import _parse_iso, get_engagement_repository
from auto_search.engagement import digest as digest_mod
from auto_search.engagement import notify

load_dotenv(override=True)
logger = logging.getLogger("run_engagement_digest")


def main() -> int:
    ap = argparse.ArgumentParser(description="Weekly 'hot movers' engagement digest -> Slack")
    ap.add_argument("--days", type=int, default=7, help="window in days (default 7)")
    ap.add_argument("--limit", type=int, default=5, help="movers to list inline (default 5)")
    ap.add_argument("--test", action="store_true", help="mark the post [TEST]")
    ap.add_argument("--dry-run", action="store_true", help="print the payload, don't post")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    repo = get_engagement_repository()
    cutoff = datetime.now(UTC) - timedelta(days=args.days)
    window = [e for e in repo.recent_events(limit=10000)
              if (_parse_iso(e.get("occurred_at")) or datetime.min.replace(tzinfo=UTC)) >= cutoff]
    scores = {r["account_id"]: r["score"] for r in repo.engaged_accounts()}
    movers = digest_mod.select_movers(window, scores)
    payload = digest_mod.build_digest(
        movers, limit=args.limit, console_url=os.getenv("ENGAGEMENT_APP_URL"), test=args.test)

    if args.dry_run:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"\n[dry-run] {len(movers)} movers in the last {args.days}d (not posted)")
        return 0
    ok = notify.post_card(payload)
    print(f"[run_engagement_digest] movers={len(movers)} posted={ok}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
