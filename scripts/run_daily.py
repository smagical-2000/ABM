"""Daily cron entry point — one scheduled run does both jobs:

    1. discovery scan   (run_discovery.py --days 1 --no-limit)   — job-posting + WARN
       + funding/leadership signals → qualify → panel
    2. social poll      (run_social.py --since-hours 24 …)        — Apify post-engagers
       on monitored accounts + event keywords → decision-maker filter → qualify → panel
    3. SFDC engagement  (run_engagement_sfdc.py)                  — read-only high-intent
       inbound leads → cross to scored/ABM → heat (idempotent; matched-only)

All legs run every time (one leg's failure never skips the others); the process
exits non-zero if ANY leg failed, so Railway flags the run. Folding them into one
cron service means there's no separate cron to deploy or babysit — point the
discovery-cron at this script.
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
    sfdc_rc = _run("run_engagement_sfdc.py", "--days", "365")
    if discovery_rc or social_rc or sfdc_rc:
        print(f"\n[run_daily] FAILED — discovery={discovery_rc} social={social_rc} "
              f"sfdc={sfdc_rc}", flush=True)
        return 1
    print("\n[run_daily] all legs OK", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
