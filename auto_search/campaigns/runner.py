"""Campaign enrollment runner — the one place I/O happens (Phase 3).

Generalizes the proven linkedin_ads_runner shape to account-driven enrollment:

    eligibility (pure, enroll.py) -> contacts (engagement store, already matched
    to accounts by Phase 2) -> Reply.io add_to_campaign (the ONE write) ->
    ledger (campaign_repository) -> Slack heads-up (best-effort).

Safety, same rules as the LinkedIn flow:
  • `dry_run=True` (default) does EVERYTHING except the Reply.io write, the
    ledger insert, and Slack — a full run can be watched producing the would-be
    enrollments with nothing sent. Dry runs are never persisted.
  • Idempotent: the ledger's (account, contact, campaign) key + Reply.io's own
    409 ("already in a sequence") mean a re-run can never double-enroll.
  • Bounded: `account_cap` accounts per run (drip-feed into Reply.io at the
    mailbox pool's real pace, never a dump), `contact_cap` per account.
  • Never raises per-contact — one failure is recorded and skipped so the run
    always completes (mirrors linkedin_ads_runner).
"""

from __future__ import annotations

import inspect
import logging
from collections import Counter
from datetime import UTC, datetime

from auto_search.campaigns import catalog, enroll
from auto_search.campaigns import rules as rules_mod

logger = logging.getLogger(__name__)

DEFAULT_ACCOUNT_CAP = 10          # accounts per run — drip, not dump
CHANNEL = "email"


def heat_by_id(engagement_repo) -> dict[str, int]:
    try:
        return {r["account_id"]: int(r.get("score") or 0)
                for r in engagement_repo.engaged_accounts() if r.get("account_id")}
    except Exception:  # noqa: BLE001 — an empty board must not block enrollment
        logger.exception("engaged_accounts read failed — treating heat as empty")
        return {}


def contacts_by_account(engagement_repo) -> dict[str, list[dict]]:
    """All matched contacts grouped by account in ONE read (no N+1)."""
    out: dict[str, list[dict]] = {}
    for c in engagement_repo.contacts():
        aid = c.get("account_id")
        if aid:
            out.setdefault(aid, []).append(c)
    return out


def _sequence_map(campaign_repo) -> dict[str, dict]:
    """sequence_key -> {campaign_id, campaign_name} for keys that ARE mapped."""
    return {r["sequence_key"]: r for r in campaign_repo.sequences()
            if r.get("campaign_id")}


def linkedin_leads_for(account: dict, *, already: set[str],
                       cap: int | None = None) -> tuple[list[dict], int]:
    """HeyReach lead payloads for one scored account: its warm-intro decision-
    makers that carry a LinkedIn profile URL (HeyReach's identity key). Email
    contacts without a LinkedIn URL can't ride this channel — expected, not an
    error. Returns (leads, skipped_no_url). Dedup by URL + the stop/enroll ledger."""
    wi = account.get("warm_intros") or {}
    contacts = wi.get("contacts") if isinstance(wi, dict) else None
    leads: list[dict] = []
    skipped_no_url = 0
    seen: set[str] = set()
    for c in contacts or []:
        url = (c.get("linkedin_url") or "").strip()
        if not url:
            skipped_no_url += 1
            continue
        ext = f"li:{url}"
        if url in seen or ext in already:
            continue
        if cap is not None and len(leads) >= cap:
            break
        seen.add(url)
        name = (c.get("name") or "").strip()
        first, _, last = name.partition(" ")
        leads.append({"profileUrl": url, "firstName": first or name,
                      "lastName": last, "companyName": account.get("name"),
                      "position": c.get("title")})
    return leads, skipped_no_url


async def run(*, campaign_repo, engagement_repo, scoring_repo,
              replyio_client=None, heyreach_client=None, dry_run: bool = True,
              account_cap: int | None = DEFAULT_ACCOUNT_CAP,
              contact_cap: int | None = None,
              only_account_id: str | None = None,
              custom_rules: list[dict] | None = None,
              trigger: str = "auto",
              fit_bands: tuple[str, ...] = enroll.DEFAULT_FIT_BANDS,
              heat_tiers: tuple[str, ...] = enroll.DEFAULT_HEAT_TIERS,
              notify_fn=None, now: str | None = None) -> dict:
    """One enrollment pass. Returns {dry_run, ran_at, stats, accounts, capped}.

    `only_account_id` is the manual path: enroll THAT scored account regardless
    of the heat/intent trigger (a human override is a deliberate act), still
    respecting mapping, opt-outs, and the ledger. `notify_fn(summary_dict)` is
    posted per enrolled account, best-effort, live runs only.
    """
    now = now or datetime.now(UTC).isoformat()
    stats: Counter = Counter()
    results: list[dict] = []

    scored = scoring_repo.list_accounts()
    scored_by_id = {a.get("account_id"): a for a in scored}
    heat = heat_by_id(engagement_repo)
    seq_map = _sequence_map(campaign_repo)
    contacts_by_acct = contacts_by_account(engagement_repo)

    # LinkedIn channel (HeyReach): per-key mapping + the connected sender seats.
    # No client / no mapping / no senders each degrade to a reported skip, never
    # an error — the leg lights up the moment MAR2-6 lands a seat.
    li_map = {r["sequence_key"]: r for r in campaign_repo.channel_sequences()
              if r.get("channel") == "linkedin" and r.get("campaign_id")}
    li_senders: list[int] = []
    if heyreach_client is not None and li_map and not dry_run:
        try:
            li_senders = [s["id"] for s in await heyreach_client.list_senders()
                          if s.get("id") and s.get("active") is not False]
        except Exception:  # noqa: BLE001 — LinkedIn leg degrades, email continues
            logger.exception("heyreach list_senders failed — linkedin leg skipped")

    if only_account_id:
        rows = [a for a in scored
                if a.get("account_id") == only_account_id and a.get("state") == "scored"]
        eligibles = []
        for a in rows:
            aid = a["account_id"]
            hs = int(heat.get(aid) or 0)
            from auto_search.engagement import scoring as engagement_scoring
            eligibles.append(enroll.Eligible(
                account_id=aid, name=a.get("name") or aid, segment=a.get("segment"),
                sequence_key=catalog.sequence_key_for(a),
                fit_band=str(a.get("tier_band") or "").lower(),
                fit_label=a.get("tier_label"), heat_score=hs,
                heat_tier=engagement_scoring.tier_for(hs),
                intent_tier=enroll.intent_tier_for(a),
                reasons=["Manually enrolled"],
            ))
        if not eligibles:
            return {"dry_run": dry_run, "ran_at": now, "stats": {"not_scored": 1},
                    "accounts": [], "capped": False}
    else:
        eligibles = enroll.eligible_accounts(
            scored, heat, exclude_ids=campaign_repo.accounts_enrolled(),
            fit_bands=fit_bands, heat_tiers=heat_tiers)

    capped = account_cap is not None and len(eligibles) > account_cap
    todo = eligibles[:account_cap] if account_cap is not None else eligibles
    routes = {e.account_id: rules_mod.resolve_route(e, custom_rules or [],
                                                    seq_map, li_map)
              for e in todo}

    for e in todo:
        stats["accounts_considered"] += 1
        route = routes[e.account_id]
        seq = route["email"]
        summary = {**e.as_dict(), "channel": "email",
                   "route": route["route_label"],
                   "campaign_id": None, "campaign_name": None,
                   "planned": 0, "enrolled": 0, "skipped_409": 0, "failed": 0,
                   "skipped": {}, "status": "unmapped"}
        if not seq:
            stats["unmapped_sequence"] += 1
            results.append(summary)
            continue                       # the ICP's sequence isn't built/mapped yet
        campaign_id = seq["campaign_id"]
        summary["campaign_id"] = campaign_id
        summary["campaign_name"] = seq.get("campaign_name")

        already = campaign_repo.enrolled_for(e.account_id, campaign_id)
        planned, skipped = enroll.plan_contacts(
            contacts_by_acct.get(e.account_id) or [], already=already, cap=contact_cap)
        summary["planned"] = len(planned)
        summary["skipped"] = {k: v for k, v in skipped.items() if v}
        if not planned:
            stats["no_sendable_contacts"] += 1
            summary["status"] = "no_contacts"
            results.append(summary)
            continue

        if dry_run:
            stats["would_enroll_accounts"] += 1
            stats["would_enroll_contacts"] += len(planned)
            summary["status"] = "dry_run"
            results.append(summary)
            continue

        # ── live: the one write, per contact; ledger after each outcome ──
        for p in planned:
            row = {"account_id": e.account_id, "account_name": e.name,
                   "contact_ext": p["contact_ext"], "email": p["email"],
                   "channel": CHANNEL, "sequence_key": e.sequence_key,
                   "campaign_id": campaign_id, "trigger": trigger,
                   "enrolled_at": now,
                   "detail": ({"rule": route["rule"]["name"]}
                              if route.get("rule") else {})}
            try:
                res = await replyio_client.add_to_campaign(
                    campaign_id=int(campaign_id), email=p["email"],
                    company=p.get("company") or e.name, title=p.get("title"))
                if isinstance(res, dict) and res.get("status") == 409:
                    row["status"] = "skipped_409"     # already sequenced — terminal
                    summary["skipped_409"] += 1
                    stats["contacts_409"] += 1
                else:
                    row["status"] = "enrolled"
                    summary["enrolled"] += 1
                    stats["contacts_enrolled"] += 1
            except Exception as exc:  # noqa: BLE001 — one contact must not sink the run
                logger.warning("enroll failed for %s -> campaign %s: %s",
                               p["email"], campaign_id, exc)
                row["status"] = "failed"
                row["detail"] = {"error": str(exc)[:200]}
                summary["failed"] += 1
                stats["contacts_failed"] += 1
            campaign_repo.add_enrollment(row)

        summary["status"] = "enrolled" if summary["enrolled"] or summary["skipped_409"] \
            else "failed"
        if summary["status"] == "enrolled":
            stats["accounts_enrolled"] += 1
        results.append(summary)

        if notify_fn is not None and summary["status"] == "enrolled":
            try:                                    # Slack heads-up — never blocks
                res = notify_fn(summary)
                if inspect.isawaitable(res):        # sync or async notifier, both fine
                    await res
            except Exception:  # noqa: BLE001
                logger.warning("enrollment Slack notify failed for %s", e.account_id)

    # ── LinkedIn leg (parallel channel): same eligible accounts, HeyReach as the
    #    executor. Leads = the account's warm-intro decision-makers WITH a
    #    LinkedIn URL, round-robined across connected seats. ──────────────────
    for e in todo:
        li_seq = routes[e.account_id]["linkedin"]
        if not li_seq or heyreach_client is None:
            continue                               # channel not mapped / not configured
        li_campaign = li_seq["campaign_id"]
        already_li = campaign_repo.enrolled_for(e.account_id, li_campaign)
        leads, no_url = linkedin_leads_for(
            scored_by_id.get(e.account_id) or {}, already=already_li, cap=contact_cap)
        li_summary = {**e.as_dict(), "channel": "linkedin",
                      "route": routes[e.account_id]["route_label"],
                      "campaign_id": li_campaign,
                      "campaign_name": li_seq.get("campaign_name"),
                      "planned": len(leads), "enrolled": 0, "skipped_409": 0,
                      "failed": 0, "skipped": ({"no_linkedin_url": no_url} if no_url else {}),
                      "status": "dry_run" if dry_run else "no_leads"}
        if not leads:
            li_summary["status"] = "no_leads"
            stats["linkedin_no_leads"] += 1
            results.append(li_summary)
            continue
        if dry_run:
            stats["linkedin_would_enroll_accounts"] += 1
            stats["linkedin_would_enroll_leads"] += len(leads)
            results.append(li_summary)
            continue
        if not li_senders:
            li_summary["status"] = "no_sender"     # seat not connected yet (MAR2-6)
            stats["linkedin_no_sender"] += 1
            results.append(li_summary)
            continue
        try:
            res = await heyreach_client.add_leads_to_campaign(
                campaign_id=int(li_campaign), leads=leads, sender_ids=li_senders)
            added = int(res.get("addedLeadsCount") or 0)
            li_summary["enrolled"] = added
            li_summary["status"] = "enrolled" if added else "failed"
            stats["linkedin_leads_enrolled"] += added
            for lead in leads:
                campaign_repo.add_enrollment({
                    "account_id": e.account_id, "account_name": e.name,
                    "contact_ext": f"li:{lead['profileUrl']}", "email": None,
                    "channel": "linkedin", "sequence_key": e.sequence_key,
                    "campaign_id": li_campaign, "trigger": trigger,
                    "status": "enrolled", "enrolled_at": now,
                    "detail": {"profile_url": lead["profileUrl"]}})
        except Exception as exc:  # noqa: BLE001 — LinkedIn leg must not sink the run
            logger.warning("heyreach enroll failed for %s: %s", e.account_id, exc)
            li_summary["status"] = "failed"
            li_summary["failed"] = len(leads)
            stats["linkedin_failed"] += 1
        results.append(li_summary)

    out = {"dry_run": dry_run, "ran_at": now, "stats": dict(stats),
           "accounts": results, "capped": capped,
           "eligible_total": len(eligibles)}
    logger.info("campaign enrollment run (%s): %s",
                "dry-run" if dry_run else "LIVE", out["stats"])
    return out
