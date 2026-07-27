"""SFDC ↔ engagement reconciliation — the label-drift tripwire.

Every silent scoring miss this month was the same disease: SFDC's labels moved
('CS Headspace | BOFU', 'TOFU Engagement Campaign') and our filters didn't, so
leads Griffen's dashboard counted never reached the engagement store — for
WEEKS, silently. This leg re-pulls the last 14 days of leads + meetings through
sync.collect_sfdc_rows — the EXACT pipeline the daily sync runs, not a fork of
it (a forked copy is how the reconcile itself could drift) — and alerts on any
signal that SHOULD be in the store but isn't. Drift now gets caught in 24
hours, loudly, with names.

Report-only: exits 0 even when misses are found (the alert is the output).
"""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from auto_search.db import get_repository
from auto_search.db.engagement_repository import get_engagement_repository
from auto_search.db.scoring_repository import get_scoring_repository
from auto_search.engagement.cross import build_index
from auto_search.engagement.sfdc_client import SalesforceClient
from auto_search.engagement.sync import collect_sfdc_rows
from auto_search.ops import alerts as ops_alerts

load_dotenv()
logger = logging.getLogger("run_reconcile_sfdc")

WINDOW_DAYS = 14


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    since = (datetime.now(UTC).date() - timedelta(days=WINDOW_DAYS)).isoformat()
    now = datetime.now(UTC).isoformat()
    erepo = get_engagement_repository()
    index = build_index(get_scoring_repository(), get_repository(), erepo)

    contact_rows, event_rows, counts = collect_sfdc_rows(
        SalesforceClient(), erepo, since=since, now=now)
    contacts_by_id = {c["external_id"]: c for c in contact_rows}
    existing = erepo.external_ids_for_source("sfdc")

    missing: list[str] = []
    for ev in event_rows:
        c = contacts_by_id.get(ev.get("contact_ext")) or {}
        m = index.match(company=c.get("company"), domain=c.get("email_domain"),
                        email=c.get("email"))
        if not m:
            continue                       # unmatched inbound is dropped by design
        if ev["external_id"] not in existing:
            missing.append(f"{ev['kind']} · {c.get('company') or '?'} · "
                           f"{c.get('email') or ev.get('contact_ext')} · {ev['external_id']}")

    print(f"[reconcile] window {since}→today: hi={counts['high_intent']} "
          f"ts={counts['tradeshow']} lo={counts['low_intent']} "
          f"meetings={counts['meetings']} | MISSING-from-store: {len(missing)}", flush=True)
    for line in missing[:20]:
        print("  MISSING:", line, flush=True)
    if missing:
        ops_alerts.post_ops_alert(
            kind="sfdc-reconcile", severity="warning", service="discovery-cron",
            title=f"{len(missing)} SFDC signal(s) missing from engagement store",
            detail="\n".join(missing[:15]),
            runbook="Run the SFDC sync leg; if these carry a NEW LeadSource label, "
                    "update sfdc_client filters + docs/RULES.md (label drift).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
