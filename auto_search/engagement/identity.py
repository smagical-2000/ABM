"""Company-identity healing — one company, one account id (MAR2-32).

cross.py promises that an abm_<key> synthetic id "self-heals to the scored id
once that company is scored" — but crossing only re-points NEW events. History
ingested before the company was scored stays on the abm id, splitting one
company's heat across twin board tiles. Tiers are computed per tile, so a
company-level Hot can hide below every threshold: Summa read 19+12 instead of
31, and CORA's genuine post-cutoff Hot never fired (the silent false negative).

heal_identity_splits() completes the self-heal for HISTORY: it re-keys
events/contacts/activations from stale twins onto the canonical id. It runs
after every cross_and_persist and after bulk imports — the two moments a split
can be born — so a split never outlives the ingest that would expose it.

Safety guards (mirroring the 2026-07-13 prod migration audit):
  - guarded company_key grouping (single-word degeneracy protected);
  - a group must contain exactly ONE non-abm id (else manual review);
  - every known domain across the group must agree (else manual review — the
    Healthfirst hf.org/healthfirst.org class is never auto-merged);
  - capability-gated: silently skips on repos without the rekey surface, so
    minimal test fakes passing through cross_and_persist are unaffected.
"""

from __future__ import annotations

import logging

from auto_search.engagement.notify import company_key
from auto_search.normalize import normalize_company_name

logger = logging.getLogger(__name__)

_NEEDED = ("engaged_accounts", "contacts", "rekey_account")


def display_maps(scoring_repo, discovery_repo) -> tuple[dict[str, str], dict[str, str]]:
    """(account_id -> display name, account_id -> domain) across scored accounts
    and ABM targets — the same naming the board uses (scored name wins; abm
    synthetic ids are minted exactly like cross.py mints them)."""
    name_of: dict[str, str] = {}
    domain_of: dict[str, str] = {}
    scored_rows = (scoring_repo.list_accounts()
                   if hasattr(scoring_repo, "list_accounts") else []) or []
    abm_rows = (discovery_repo.abm_targets()
                if hasattr(discovery_repo, "abm_targets") else []) or []
    for a in scored_rows:
        aid = a.get("account_id")
        if not aid:
            continue
        if a.get("name"):
            name_of.setdefault(aid, a["name"])
        if a.get("domain"):
            domain_of.setdefault(aid, str(a["domain"]).strip().lower())
    for t in abm_rows:
        nm = t.get("name") or ""
        key = normalize_company_name(nm)
        if not key:
            continue
        aid = f"abm_{key}"     # exactly how cross.py mints the synthetic id
        name_of.setdefault(aid, nm)
        if t.get("domain"):
            domain_of.setdefault(aid, str(t["domain"]).strip().lower())
    return name_of, domain_of


def heal_identity_splits(engagement_repo, scoring_repo, discovery_repo, *,
                         dry_run: bool = False) -> dict:
    """Merge twin identities onto their canonical (scored) account id.

    Returns {"merged": {old_id: new_id}, "manual": [...], "events"/"contacts"/
    "activations": moved counts, "dry_run": bool} — or {"skipped": reason} when
    a repo lacks the needed surface. dry_run computes without moving anything.
    """
    if not all(hasattr(engagement_repo, m) for m in _NEEDED):
        return {"skipped": "engagement repo lacks rekey surface", "merged": {}}

    name_of, domain_of = display_maps(scoring_repo, discovery_repo)

    ids: set[str] = {r["account_id"] for r in engagement_repo.engaged_accounts()}
    contact_company: dict[str, str] = {}
    for c in engagement_repo.contacts():
        aid = c.get("account_id")
        if aid:
            ids.add(aid)
            if c.get("company"):
                contact_company.setdefault(aid, c["company"])

    groups: dict[str, list[str]] = {}
    for aid in ids:
        display = name_of.get(aid) or contact_company.get(aid) or ""
        key = company_key(display)
        if key:
            groups.setdefault(key, []).append(aid)

    merged: dict[str, str] = {}
    manual: list[dict] = []
    moved = {"events": 0, "contacts": 0, "activations": 0}
    for key, group in sorted(groups.items()):
        if len(group) < 2:
            continue
        canon = [i for i in group if not i.startswith("abm_")]
        twins = [i for i in group if i.startswith("abm_")]
        if len(canon) != 1 or not twins:
            manual.append({"company": key, "ids": sorted(group),
                           "why": "no single canonical id"})
            continue
        doms = {domain_of[i] for i in group if domain_of.get(i)}
        if len(doms) > 1:
            manual.append({"company": key, "ids": sorted(group),
                           "why": f"conflicting domains {sorted(doms)}"})
            continue
        for old in twins:
            merged[old] = canon[0]
            if not dry_run:
                got = engagement_repo.rekey_account(old, canon[0])
                for k in moved:
                    moved[k] += got.get(k, 0)
                logger.info("identity heal: %s -> %s (%s)", old, canon[0], got)
    return {"merged": merged, "manual": manual, **moved, "dry_run": dry_run}
