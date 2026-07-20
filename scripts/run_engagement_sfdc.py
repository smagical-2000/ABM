"""Daily SFDC engagement pull — high-intent inbound leads (read-only).

Pulls the org's High Intent Leads (contact/sales-form LeadSources) from the last
`--days`, crosses them to scored + ABM accounts, and rolls them into the same heat
tiers as Reply.io + podcast. Read-only against Salesforce (SOQL SELECT only).

Idempotent by design — events are keyed by `form:high_intent_lead:<LeadId>`, so a
daily re-pull of the rolling window upserts the same rows and never duplicates
(a lead whose data changed is updated in place). Only leads that match an account
are persisted; non-target inbound is dropped, not queued.

This is an entry point the daily Railway cron calls (see railway.cron.json /
run_daily.py). Needs SFDC_CLIENT_ID/SECRET/LOGIN_URL + DATABASE_URL in the env.

Run:
    python scripts/run_engagement_sfdc.py --since 2026-01-01
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from auto_search.db import get_repository
from auto_search.db.engagement_repository import get_engagement_repository
from auto_search.db.scoring_repository import get_scoring_repository
from auto_search.engagement import sync as engagement_sync

load_dotenv()   # no override: operator env (e.g. DATABASE_URL) must win
logger = logging.getLogger("run_engagement_sfdc")


def main() -> int:
    ap = argparse.ArgumentParser(description="Daily SFDC high-intent-lead engagement pull")
    ap.add_argument("--since", default="2026-01-01",
                    help="only leads created on/after this date, YYYY-MM-DD (default 2026-01-01)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    engagement_repo = get_engagement_repository()
    engagement_repo.ensure_schema()
    try:
        stats = engagement_sync.run_sfdc_sync(
            engagement_repo=engagement_repo,
            scoring_repo=get_scoring_repository(),
            discovery_repo=get_repository(),
            since=args.since)
    except Exception:  # noqa: BLE001 — cron leg: log + signal failure, don't traceback-crash
        logger.exception("[run_engagement_sfdc] SFDC sync failed")
        return 1
    print(f"[run_engagement_sfdc] ok: {stats}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
