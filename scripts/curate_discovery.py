#!/usr/bin/env python3
"""Curate the Discovery panel down to strong-signal leads — reversible.

Rejects qualified + pending leads that are weak signals (a single junior-role job
posting at a non-target company — e.g. "Medical Payment Posting Coordinator"),
keeping only the leads worth showing:

  * ABM-confirmed target accounts (on the list, found independently)
  * stacked companies (2+ signals / multiple open revenue-cycle roles)
  * high-intent signals (social engagement, leadership change, layoff)
  * senior-role postings (Director / VP / Manager / Supervisor / Chief / ...)

`reject` sets review_status='rejected' while leaving icp_status='qualified', so:
  - the lead drops off the qualified panel immediately,
  - the daily discovery-cron never re-qualifies it (dedup keys off icp_status),
  - POST /api/company/<key>/restore brings it back (nothing is deleted).

Idempotent + re-runnable: already-rejected leads aren't in the panel, and any
fresh cron leads get classified on the next run.

Usage (creds from env; base defaults to the prod service):
    BASIC_AUTH_USER=… BASIC_AUTH_PASS=… python scripts/curate_discovery.py --dry-run
    BASIC_AUTH_USER=… BASIC_AUTH_PASS=… python scripts/curate_discovery.py --apply
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import urllib.parse
import urllib.request

BASE = os.getenv("DISCOVERY_API_BASE",
                 "https://discovery-api-production-dc7f.up.railway.app")

# Title keywords that mark a posting as a real buyer-adjacent role (not junior).
SENIOR = ("director", "vp", "vice president", "chief", " head", "head ",
          "manager", "supervisor", " lead", "administrator", "executive",
          "president", "officer")

REASON = "Curated out: low-signal single junior-role, non-target (reversible via restore)"


def _req(path: str, method: str = "GET", body: dict | None = None) -> dict:
    user, pw = os.getenv("BASIC_AUTH_USER", ""), os.getenv("BASIC_AUTH_PASS", "")
    req = urllib.request.Request(BASE + path, method=method)
    token = base64.b64encode(f"{user}:{pw}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data=data, timeout=30) as r:  # noqa: S310 — fixed host
        return json.loads(r.read() or "{}")


def keep_reason(row: dict) -> str | None:
    """Why this lead is worth keeping, or None if it's weak (reject it)."""
    sigs = row.get("signals") or []
    if len(sigs) >= 2:
        return f"stacked({len(sigs)})"
    if (row.get("abm_match") or {}).get("tier") == "confirmed":
        return "abm-confirmed"
    for s in sigs:
        if s.get("signal_type") != "job_posting":
            return "high-intent:" + (s.get("signal_type") or "")
        if any(k in (s.get("title") or "").lower() for k in SENIOR):
            return "senior-role"
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--dry-run", action="store_true", help="show keep/reject, change nothing")
    grp.add_argument("--apply", action="store_true", help="reject the weak leads")
    args = ap.parse_args()

    rows = _req("/api/panel?status=qualified")
    keep = [r for r in rows if keep_reason(r)]
    weak = [r for r in rows if not keep_reason(r)]
    print(f"panel: {len(rows)} qualified+pending  ->  keep {len(keep)}, reject {len(weak)}")

    if args.dry_run:
        for r in weak:
            t = (r.get("signals") or [{}])[0].get("title") or "?"
            print(f"  REJECT  {r['name'][:38]:38} | {t[:46]}")
        print(f"\n(dry run — nothing changed; {len(weak)} would be rejected)")
        return 0

    done = 0
    for r in weak:
        key = urllib.parse.quote(r["company_key"], safe="")
        try:
            _req(f"/api/company/{key}/reject", "POST", {"reason": REASON})
            done += 1
        except Exception as e:  # noqa: BLE001 — report and continue the batch
            print(f"  ! failed {r['name']}: {e}")
    print(f"rejected {done}/{len(weak)}; the panel now shows {len(keep)} strong leads")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
