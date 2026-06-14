"""Daily cron entry point — one scheduled run does both jobs:

    1. discovery scan   (run_discovery.py --days 1 --no-limit)   — job-posting + WARN
       + funding/leadership signals → qualify → panel
    2. social poll      (run_social.py --since-hours 24 …)        — Apify post-engagers
       on monitored accounts + event keywords → decision-maker filter → qualify → panel

Both run every time (a discovery failure never skips the social poll); the
process exits non-zero if EITHER leg failed, so Railway flags the run. Folding
both into one cron service means there's no separate social-cron to deploy or
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
    if discovery_rc or social_rc:
        print(f"\n[run_daily] FAILED — discovery={discovery_rc} social={social_rc}", flush=True)
        return 1
    print("\n[run_daily] both legs OK", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
