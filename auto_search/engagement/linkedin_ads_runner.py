"""LinkedIn TOFU ad-engagement runner — the hourly I/O pipeline.

Per post: scrape people who REACTED (Apify `harvestapi~linkedin-post-reactions`) ->
tag each reactor with the post's share_id/category -> dedupe people -> enrich profile
to company+domain (Apify freshdata via social.apify.enrich) -> drop Magical's own
staff (social.filters.is_magical) -> ABM-only gate (engagement.cross; capturing
non-ABM reactors too is proposed, pending Galyna — see Linear) -> Apollo work
email + phone (2026-07-08: email OR phone qualifies a lead; FullEnrich phone
fallback saves the no-email ones) -> WRITE: Airtable upsert (the "LinkedIn <>
Airtable" table; downstream automation takes it from there), then Reply.io
add-to-campaign (email leads) -> record `linkedin_tofu` heat (6 pts).

We push to Airtable, NOT Salesforce (per the 2026-06 change): SFDC creation is handled
by the user's Airtable automation. Reply.io and the heat capture are kept.

`dry_run=True` (default) does EVERYTHING EXCEPT the writes (Airtable, Reply.io, heat
persist), so we can watch a full run produce the would-be rows with nothing written.

Idempotency (survives hourly re-runs):
  - profile-id gate: a person we already pushed is skipped BEFORE any paid step (we
    load their engagement contacts at run start). Only a SUCCESSFUL push persists the
    contact, so a failed run retries next hour rather than zombie-scoring.
  - Airtable upsert merges on Email (or LinkedIn URL for phone-only leads), so even
    a person not in our store (e.g. an external import) updates their row instead
    of duplicating it.

Cost is bounded by `max_reactions` (per post) + `max_contacts` (people per run);
enrichment + Apollo run only for not-yet-processed people, ABM survivors get the
writes. FullEnrich runs for leads missing a phone (for a no-email lead it's the
lookup that qualifies them); dead-ends persist a contact row so they are never
re-billed on the next scan.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from datetime import UTC, datetime

from auto_search.engagement import enrichment, notify, scoring
from auto_search.engagement import linkedin_ads as la
from auto_search.engagement.cross import build_index
from auto_search.engagement.sync import cross_and_persist
from auto_search.normalize import clean_domain, normalize_company_name
from auto_search.scoring import apollo
from auto_search.social import apify as social_apify
from auto_search.social.filters import is_magical

logger = logging.getLogger(__name__)

SOURCE = "linkedin_ads"
CHANNEL = "linkedin"


def _split_name(full: str | None) -> tuple[str | None, str | None]:
    parts = (full or "").strip().split()
    if not parts:
        return None, None
    if len(parts) == 1:
        return None, parts[0]
    return parts[0], " ".join(parts[1:])


def _host(value: str | None) -> str | None:
    """Bare registrable domain from a website value that may be a full URL or carry
    `www.` (Apify enrichment returns `company_website` URLs). clean_domain alone keeps
    the scheme/path/www, so a website would never match a scored/ABM domain."""
    v = (value or "").strip().lower()
    if not v:
        return None
    v = v.split("://", 1)[-1].split("/", 1)[0].split("?", 1)[0]
    if v.startswith("www."):
        v = v[4:]
    return clean_domain(v)


async def _scrape(share_categories: dict[str, str], *, max_reactions: int) -> list[dict]:
    """Reactions across all posts, each tagged with its share_id + category, deduped to
    one row per person. A person who liked several posts is kept once, under the
    first post in `share_categories` order (deterministic; v1 doesn't split a person
    across categories)."""
    by_person: dict[str, dict] = {}
    for share_id, category in share_categories.items():
        try:
            rs = await social_apify.fetch_post_reactions(
                la.post_url(share_id), max_items=max_reactions)
        except Exception as e:  # noqa: BLE001 — one bad post mustn't sink the run
            logger.warning("reactions fetch failed for %s: %s", share_id, e)
            continue
        for r in rs:
            pid = r.get("profile_id") or r.get("linkedin_url")
            if not pid or pid in by_person:
                continue
            r["share_id"], r["category"] = share_id, category
            by_person[pid] = r
    return list(by_person.values())


async def run(*, share_categories: dict[str, str], engagement_repo, scoring_repo,
              discovery_repo, airtable_client=None, replyio_client=None,
              mirror_client=None, max_reactions: int = 50,
              max_contacts: int | None = None,
              max_leads: int | None = None, dry_run: bool = True,
              now: str | None = None) -> dict:
    """Run the pipeline. Returns {dry_run, stats, results}. Never raises per-contact —
    one failure is counted and skipped so the batch always completes. `max_leads` stops
    after that many leads are created/would-be-created (e.g. 1 for the live spot-check)."""
    now = now or datetime.now(UTC).isoformat()
    index = build_index(scoring_repo, discovery_repo)        # ABM / scored cross

    # Durable per-person dedup: contact external_ids we've already persisted.
    processed: set[str] = set()
    if engagement_repo is not None:
        try:
            processed = {c.get("external_id") for c in engagement_repo.contacts()}
        except Exception as e:  # noqa: BLE001 — a missing store mustn't break the run
            logger.warning("could not load processed contacts: %s", e)

    candidates = await _scrape(share_categories, max_reactions=max_reactions)
    if max_contacts:                                          # caps PEOPLE per run
        candidates = candidates[:max_contacts]

    stats: Counter = Counter()
    results: list[dict] = []
    contact_rows: list[dict] = []
    event_rows: list[dict] = []
    leads = 0                                   # for the max_leads cap

    for r in candidates:
        stats["scanned"] += 1
        url = r.get("linkedin_url")
        pid = r.get("profile_id") or url
        if not (url and pid):
            stats["no_url"] += 1
            continue
        if f"{CHANNEL}:{pid}" in processed:                  # already a lead — skip pre-spend
            stats["already_processed"] += 1
            continue

        try:
            enr = await social_apify.enrich(url) or {}
        except Exception as e:  # noqa: BLE001 — one bad profile mustn't sink the run
            logger.warning("enrich failed for %s: %s", url, e)
            stats["enrich_failed"] += 1
            continue
        company = enr.get("company")
        domain = _host(enr.get("company_domain"))
        enriched_url = enr.get("linkedin_url") or url
        if is_magical(company, domain, url, enriched_url):
            stats["dropped_magical"] += 1
            continue
        if not (company or domain):
            stats["no_company"] += 1
            continue

        # ABM-only gate: keep only people whose company is an ABM target.
        # (Capturing non-ABM reactors too is PROPOSED, 2026-07-08 — Linear
        # ticket pending Galyna's decision; until then they are dropped here.)
        m = index.match(company=company, domain=domain)
        if not (m and "abm" in m.lists):
            stats["not_abm"] += 1
            continue

        first, last = _split_name(enr.get("full_name") or r.get("name"))
        display = enr.get("full_name") or r.get("name")
        if not (first or last):                              # no usable name — don't create junk
            stats["no_name"] += 1
            continue

        ap = await apollo.match_contact(
            linkedin_url=enriched_url, first_name=first, last_name=last, domain=domain) or {}
        email = ap.get("email")
        phone = ap.get("phone")
        title = ap.get("title") or enr.get("job_title") or r.get("position")

        company = company or m.name
        campaign_id = la.campaign_for(r["category"])
        # FullEnrich phone fallback — real runs only (no dry-run spend). Runs
        # when the lead has no phone: for a lead WITH an email it adds the
        # mobile; for a lead WITHOUT one it's the lookup that QUALIFIES them
        # (2026-07-08 rule: email OR phone makes a lead).
        if not phone and not dry_run:
            fe = await enrichment.enrich_contact(
                first_name=first, last_name=last, domain=domain,
                company=company, linkedin=enriched_url)
            if fe.get("phone"):
                phone = fe["phone"]
                stats["fullenrich_phone"] += 1
        if not (email or phone):
            stats["no_email_or_phone"] += 1     # nothing to reach them by — not a lead
            # Persist the contact anyway (no lead, no heat): it's the durable
            # dedup key, so this dead-end isn't re-billed through Apollo +
            # FullEnrich every 15 minutes. Clay waterfall re-attempts later.
            if not dry_run:
                contact_rows.append(_contact_row(r, enr, email, domain, company, title))
            continue

        outcome = {
            "name": display, "email": email, "phone": phone, "title": title,
            "company": company, "domain": domain, "category": r["category"],
            "campaign_id": campaign_id, "account_id": m.account_id,
            "share_id": r["share_id"], "airtable_id": None,
        }

        if dry_run:
            stats["would_create"] += 1
            results.append(outcome)
            leads += 1
            if max_leads and leads >= max_leads:
                break
            continue

        # Slack heads-up BEFORE the lead is written to Airtable (Airtable then creates
        # the Salesforce lead via its own automation). So the team sees every TOFU lead
        # the moment it enters the pipeline, ahead of SFDC. Best-effort + off-loop (the
        # poster is sync) so a Slack hiccup never blocks or slows the write.
        if await asyncio.to_thread(notify.notify_lead, {
                "name": display, "title": title, "company": company, "email": email,
                "phone": phone, "linkedin": enriched_url,
                "segment": la.segment_for(r["category"])}):
            stats["slack_notified"] += 1

        # ── writes: Airtable first (the sink). Heat + Reply.io only if it lands, so a
        #    failed push never zombie-scores an account or pushes outreach. Upsert
        #    merges on Email when we have one, else LinkedIn URL (phone-only leads),
        #    so a re-run updates the row instead of duplicating. ──
        try:
            fields = la.build_airtable_fields(
                email=email, company=company, first_name=first, last_name=last,
                title=title, phone=phone, linkedin_url=enriched_url)
            res = await airtable_client.upsert(
                fields, merge_on=["Email"] if email else ["LinkedIn URL"])
            outcome["airtable_id"] = airtable_client.record_id(res)
            stats["airtable_upserted"] += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("airtable upsert failed for %s: %s", email or enriched_url, e)
            stats["airtable_failed"] += 1
            results.append(outcome)
            continue

        # Tracking mirror (Galyna, 2026-07-08): the same row is ALSO written to
        # the "TOFU Leads by ABM" base, stamped Synced At, so the team can audit
        # that the workflow misses nothing. Strictly best-effort: a mirror
        # failure never blocks the lead (primary row already landed above), it
        # is counted and ops-alerted by the caller.
        if mirror_client is not None:
            try:
                await mirror_client.upsert(
                    {**fields, "Synced At": now},
                    merge_on=["Email"] if email else ["LinkedIn URL"])
                stats["mirror_upserted"] += 1
            except Exception as e:  # noqa: BLE001
                logger.warning("mirror upsert failed for %s: %s", email or enriched_url, e)
                stats["mirror_failed"] += 1

        try:
            # Reply.io is an EMAIL sequencer: phone-only leads can't enroll —
            # they stay visible in Airtable for the SDRs (and Clay later).
            if replyio_client is not None and campaign_id and email:
                res = await replyio_client.add_to_campaign(
                    campaign_id=campaign_id, email=email, first_name=first,
                    last_name=last, company=company, title=title, phone=phone)
                if isinstance(res, dict) and res.get("status") == 409:
                    stats["replyio_already_sequenced"] += 1   # in another sequence; left as-is
                else:
                    stats["replyio_added"] += 1
            elif not email:
                stats["replyio_skipped_no_email"] += 1
        except Exception as e:  # noqa: BLE001 — lead already created; campaign add is best-effort
            logger.warning("reply.io add failed for %s: %s", email, e)
            stats["replyio_failed"] += 1

        contact_rows.append(_contact_row(r, enr, email, domain, company, title))
        event_rows.append(_event_row(r, outcome, now))
        results.append(outcome)
        leads += 1
        if max_leads and leads >= max_leads:
            break

    if not dry_run and contact_rows:
        matched, new_events = cross_and_persist(
            engagement_repo=engagement_repo, scoring_repo=scoring_repo,
            discovery_repo=discovery_repo, contact_rows=contact_rows, event_rows=event_rows)
        stats["heat_matched"] += matched
        stats["heat_events"] += new_events

    summary = {"dry_run": dry_run, "stats": dict(stats), "results": results}
    logger.info("linkedin_ads run: %s", summary["stats"])
    return summary


def _contact_row(r: dict, enr: dict, email: str, domain: str | None,
                 company: str | None, title: str | None) -> dict:
    pid = r.get("profile_id") or r.get("linkedin_url") or email
    return {
        "source": SOURCE, "external_id": f"{CHANNEL}:{pid}", "email": email,
        "email_domain": domain or _email_domain(email),
        "company": company, "company_key": normalize_company_name(company or ""),
        "title": title, "meeting_booked": False, "opted_out": False,
        "sent": 0, "delivered": 0, "opened": 0, "clicked": 0, "replied": 0, "bounced": 0,
    }


def _event_row(r: dict, outcome: dict, now: str) -> dict:
    pid = r.get("profile_id") or r.get("linkedin_url") or outcome["email"]
    return {
        "source": SOURCE, "external_id": f"{CHANNEL}:{la.HEAT_KIND}:{pid}",
        "channel": CHANNEL, "kind": la.HEAT_KIND,
        "points": scoring.points_for(la.HEAT_KIND), "contact_ext": f"{CHANNEL}:{pid}",
        "company": outcome.get("company"), "campaign": la.UTM_CAMPAIGN,
        "occurred_at": now,
        "raw": {"share_id": r.get("share_id"), "category": r.get("category"),
                "reaction_type": r.get("reaction_type"), "linkedin_url": r.get("linkedin_url"),
                "airtable_id": outcome.get("airtable_id")},
    }


def _email_domain(email: str | None) -> str | None:
    e = (email or "").strip().lower()
    return clean_domain(e.rsplit("@", 1)[-1]) if "@" in e else None
