"""Daily cron entry point — one scheduled run does both jobs:

    1. discovery scan   (run_discovery.py --days 1 --no-limit)   — job-posting + WARN
       + funding/leadership signals → qualify → panel
    2. social poll      (run_social.py --since-hours 24 …)        — Apify post-engagers
       on monitored accounts + event keywords → decision-maker filter → qualify → panel
    3. SFDC engagement  (run_engagement_sfdc.py)                  — read-only high-intent
       inbound leads → cross to scored/ABM → heat (idempotent; matched-only)
    4. Podcast leads    (run_engagement_podcast.py)               — read-only published
       CSV → cross to scored/ABM → heat (idempotent; no-ops without PODCAST_CSV_URL)
    5. Competitor news  (run_competitor_news.py)                  — Google News RSS
       distress scan on monitored competitors → news_items (fast-follower play)
    6. AE/SDR notify    (run_engagement_notify.py)                — after the syncs
       recompute tiers, fire NEW upward tier changes to Slack (kill-switched off by
       default; capped; ledger-deduped so nothing re-fires). Best-effort.

Legs 1-5 run every time (one leg's failure never skips the others); the process
exits non-zero if ANY of them failed, so Railway flags the run. Leg 6 is
best-effort — a notify failure is logged but never fails the daily run (a Slack
hiccup must not mask a good sync), and it stays a no-op until explicitly enabled.
Folding them into one cron service means there's no separate cron to deploy or
babysit — point the discovery-cron at this script.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent


def _run(script: str, *args: str) -> int:
    print(f"\n=== {script} {' '.join(args)} ===", flush=True)
    return subprocess.run([sys.executable, str(_SCRIPTS / script), *args]).returncode


def main() -> int:
    discovery_rc = _run("run_discovery.py", "--days", "1", "--no-limit")
    social_rc = _run("run_social.py", "--since-hours", "24", "--max-enrich", "100")
    sfdc_rc = _run("run_engagement_sfdc.py", "--since", "2026-01-01")
    podcast_rc = _run("run_engagement_podcast.py")
    competitor_rc = _run("run_competitor_news.py")
    # Best-effort AE/SDR handoff — runs AFTER the syncs so tiers are current. A Slack
    # failure here must NOT fail the daily run, so its rc is logged, not gated on.
    notify_rc = _run("run_engagement_notify.py")
    if discovery_rc or social_rc or sfdc_rc or podcast_rc or competitor_rc:
        print(f"\n[run_daily] FAILED — discovery={discovery_rc} social={social_rc} "
              f"sfdc={sfdc_rc} podcast={podcast_rc} competitor={competitor_rc}", flush=True)
        return 1
    if notify_rc:
        print(f"\n[run_daily] syncs OK; notify leg rc={notify_rc} (non-fatal)", flush=True)
    print("\n[run_daily] all legs OK", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
