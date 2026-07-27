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


def _corporate_email_domain(email: str | None) -> str | None:
    """Registrable corporate domain of a contact email (personal providers
    excluded) — evidence for the heal's identity checks."""
    from auto_search.engagement.cross import _email_domain
    return _email_domain(email)


def _pairs_verified(ev_a: set[str], ev_b: set[str], same_pairs: set[str]) -> bool:
    """True when at least one cross-side evidence pair is site-verified same."""
    from auto_search.engagement.site_verify import pair_key
    return any(pair_key(a, b) in same_pairs for a in ev_a for b in ev_b)


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
    email_doms: dict[str, set[str]] = {}
    for c in engagement_repo.contacts():
        aid = c.get("account_id")
        if aid:
            ids.add(aid)
            if c.get("company"):
                contact_company.setdefault(aid, c["company"])
            edom = _corporate_email_domain(c.get("email"))
            if edom:
                email_doms.setdefault(aid, set()).add(edom)
    # Raw-events universe (2026-07-27): the board rollup hides accounts whose
    # only events are deprecated kinds, so the heal never saw those splits
    # (abm_intermountainhealth). Derive ids from raw events when the repo can.
    if hasattr(engagement_repo, "event_account_ids"):
        try:
            ids.update(engagement_repo.event_account_ids())
        except Exception:  # noqa: BLE001 — universe widening is best-effort
            logger.warning("heal: event_account_ids unavailable", exc_info=True)

    groups: dict[str, list[str]] = {}
    contact_named: set[str] = set()
    for aid in ids:
        display = name_of.get(aid) or contact_company.get(aid) or ""
        if not name_of.get(aid) and contact_company.get(aid):
            contact_named.add(aid)
        key = company_key(display)
        if key:
            groups.setdefault(key, []).append(aid)

    from auto_search.engagement import site_verify
    same_pairs = site_verify.verified_same_pairs(engagement_repo)
    diff_pairs = site_verify.verified_different_pairs(engagement_repo)

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
            # Site-verified pairs resolve the conflict without a human
            # (2026-07-27): a same-company verdict (kp.org class) lets the
            # merge proceed; a different-company verdict (Healthfirst class)
            # means KEEP SEPARATE forever — logged, not re-queued.
            pairs = {site_verify.pair_key(a, b)
                     for a in doms for b in doms if a < b}
            if pairs and pairs <= same_pairs:
                pass  # verified same company — fall through to the merge
            elif pairs & diff_pairs:
                logger.info("heal: %s verified different companies, keeping "
                            "separate (%s)", key, sorted(doms))
                continue
            else:
                manual.append({"company": key, "ids": sorted(group),
                               "domains": sorted(doms),
                               "why": f"conflicting domains {sorted(doms)}"})
                continue
        # Evidence check for the name-only path (2026-07-27 audit): stored
        # domains agree or are missing, but contact EMAIL domains are evidence
        # too — a twin whose corporate emails contradict the canon's identity
        # must not auto-merge. Contact-NAMED twins (display from a free-text
        # company string) additionally require positive corroboration.
        ev_canon = ({domain_of[canon[0]]} if domain_of.get(canon[0]) else set()) \
            | email_doms.get(canon[0], set())
        bad = None
        for tw in twins:
            ev_twin = ({domain_of[tw]} if domain_of.get(tw) else set()) \
                | email_doms.get(tw, set())
            if ev_canon and ev_twin and not (ev_canon & ev_twin) \
                    and not _pairs_verified(ev_canon, ev_twin, same_pairs):
                bad = f"email-domain evidence disagrees ({tw})"
            elif tw in contact_named and not (ev_canon & ev_twin):
                bad = f"contact-named twin lacks domain corroboration ({tw})"
        if bad:
            manual.append({"company": key, "ids": sorted(group), "why": bad})
            continue
        for old in twins:
            merged[old] = canon[0]
            if not dry_run:
                got = engagement_repo.rekey_account(old, canon[0])
                for k in moved:
                    moved[k] += got.get(k, 0)
                logger.info("identity heal: %s -> %s (%s)", old, canon[0], got)
    # Observability marker (MAR2-32 v2): every REAL heal stamps when it ran and
    # what it did. The audit's I5 compares this against the newest ingest — a
    # writer that lands rows with no follow-up heal (the 2026-07-14 stale
    # discovery-cron container) turns the board red instead of silently
    # splitting identities. A crashed heal writes no marker; I5 catches that
    # too. Best-effort: the marker must never fail a heal.
    if not dry_run and hasattr(engagement_repo, "set_setting"):
        try:
            import json
            from datetime import UTC, datetime
            engagement_repo.set_setting("identity_heal_last", json.dumps(
                {"at": datetime.now(UTC).isoformat(),
                 "merged": len(merged), "manual": len(manual)}))
        except Exception:  # noqa: BLE001
            logger.exception("identity heal marker write failed")
    return {"merged": merged, "manual": manual, **moved, "dry_run": dry_run}
