"""Unresolved-contact triage — turn the silent graveyard into a review queue.

~1,300 engagement contacts never crossed to an account. Some are genuinely
non-ICP; some are the Sentara/Ascension/Nationwide class — real target-company
people lost to a name variant. This clusters the unresolved by email domain,
asks the (domain-first) index for a match proposal per cluster, and prints a
reviewable list: approve a proposal → add the domain/alias to the target →
the next sync attaches the whole cluster.

Read-only. Run weekly or after a domain backfill.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from auto_search.db import get_repository
from auto_search.db.engagement_repository import get_engagement_repository
from auto_search.db.scoring_repository import get_scoring_repository
from auto_search.engagement.cross import build_index

load_dotenv()
logger = logging.getLogger("run_unresolved_triage")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    erepo = get_engagement_repository()
    index = build_index(get_scoring_repository(), get_repository())
    unresolved = erepo.contacts(unresolved_only=True)

    clusters: dict[str, list[dict]] = {}
    for c in unresolved:
        d = (c.get("email_domain") or "").strip().lower()
        if d:
            clusters.setdefault(d, []).append(c)

    print(f"[triage] {len(unresolved)} unresolved contacts across "
          f"{len(clusters)} domains — top 25 by size:\n", flush=True)
    ranked = sorted(clusters.items(), key=lambda kv: -len(kv[1]))[:25]
    proposals = 0
    for dom, rows in ranked:
        sample = rows[0]
        m = index.match(company=sample.get("company"), domain=dom,
                        email=sample.get("email"))
        if m:
            proposals += 1
            print(f"  PROPOSE  {dom:34} ×{len(rows):<3} → {m.account_id} "
                  f"({m.name}) [{m.tier}-match]", flush=True)
        else:
            print(f"  no-match {dom:34} ×{len(rows):<3} company≈"
                  f"{str(sample.get('company'))[:36]}", flush=True)
    print(f"\n[triage] {proposals} clusters have a proposed home — approving one "
          "means adding the domain to that target, then re-running the sync.",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
