"""Auto AE/SDR tier-change notifier — the daily leg (runs after the syncs recompute
tiers). Fires ONLY new upward tier changes via the already-tested
/api/engagement/notify-changes endpoint (Some/Warm -> SDR, Hot -> AE, Hot terminal,
ledger-deduped so nothing that was already sent can re-fire).

SAFETY — the shared #abm-activated-accounts channel must never be spammed:
  1. DISABLED by default — a live send is a no-op unless ENGAGEMENT_NOTIFY_ENABLED=1
     (kill switch). Deploying this leg does nothing until you flip that on.
  2. CAP — sends at most ENGAGEMENT_NOTIFY_MAX (default 20) per run, so even a bug that
     marked everything "due" can't mass-post; the overflow is logged, not sent.
  3. SEND-ONLY — it never seeds / re-baselines, so the ledger keeps blocking re-fires
     (a re-run with no new rises posts 0).
  --dry-run reports what WOULD fire and sends nothing — always test with this first.
"""

from __future__ import annotations

import argparse
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()   # no override: operator env (e.g. DATABASE_URL) must win


def main() -> int:
    ap = argparse.ArgumentParser(description="Auto tier-change AE/SDR notifier")
    ap.add_argument("--dry-run", action="store_true", help="report only; send nothing")
    args = ap.parse_args()

    if not args.dry_run and os.getenv("ENGAGEMENT_NOTIFY_ENABLED") != "1":
        print("[run_engagement_notify] DISABLED — set ENGAGEMENT_NOTIFY_ENABLED=1 to send. "
              "No-op.", flush=True)
        return 0
    base = (os.getenv("ENGAGEMENT_APP_URL") or "").rstrip("/")
    if not base:
        print("[run_engagement_notify] ENGAGEMENT_APP_URL not set — skip.", flush=True)
        return 0
    cap = int(os.getenv("ENGAGEMENT_NOTIFY_MAX", "20"))
    user, pw = os.getenv("ENGAGEMENT_API_USER"), os.getenv("ENGAGEMENT_API_PASS")
    auth = (user, pw) if user and pw else None
    params = {"dry_run": "true"} if args.dry_run else {"limit": str(cap)}
    try:
        r = httpx.post(f"{base}/api/engagement/notify-changes", params=params,
                       auth=auth, timeout=120)
        r.raise_for_status()
        d = r.json()
    except Exception as e:  # noqa: BLE001 — best-effort leg; never crash the daily run
        print(f"[run_engagement_notify] request failed: {e}", flush=True)
        return 1
    if args.dry_run:
        print(f"[run_engagement_notify] dry-run: {d.get('due')} would fire (cap {cap}).",
              flush=True)
    else:
        print(f"[run_engagement_notify] sent {d.get('posted')} of {d.get('due')} due "
              f"(cap {cap}, live={d.get('live')}).", flush=True)
        if (d.get("due") or 0) > cap:
            print(f"[run_engagement_notify] WARNING: {d['due']} due exceeded the cap {cap} — "
                  "capped this run; investigate before the next run.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
