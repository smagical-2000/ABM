"""Engagement sync — orchestrate the read-only pull -> normalize -> cross -> store.

Ties the pieces together: replyio_client (pull) -> ingest (normalize) -> cross
(match to scored/ABM) -> engagement_repository (store). Read-only against Reply.io;
idempotent (re-running upserts, never duplicates); records each run in
engagement_sync_state so the UI can show "last synced".

This is the one place I/O happens for the engagement pull — the modules it calls
are all pure, so it stays thin and testable with injected fakes.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from auto_search.engagement import ingest as ingest_mod
from auto_search.engagement import podcast as podcast_mod
from auto_search.engagement import sfdc as sfdc_mod
from auto_search.engagement.cross import build_index
from auto_search.engagement.replyio_client import ReplyioClient, default_window

logger = logging.getLogger(__name__)

SOURCE = "replyio"
PODCAST_SOURCE = "podcast"
SFDC_SOURCE = "sfdc"
_MIN_ACTIVITY_ROWS = 20      # below this over `days`, widen the window to 60d


def cross_and_persist(*, engagement_repo, scoring_repo, discovery_repo,
                      contact_rows: list[dict], event_rows: list[dict],
                      persist_unmatched: bool = False) -> tuple[int, int]:
    """Cross each contact to a scored/ABM account, stamp the result onto the
    contact's events, then upsert contacts + add events. Returns
    (matched_contacts, new_events). Shared by every source's sync.

    Policy (per the user): we only TRACK companies on the ABM list or the scored/
    discovery list, so by default we store only contacts/events that matched one of
    those — unmatched engagement is dropped, not queued. The matched count is still
    returned in full either way. Pass `persist_unmatched=True` to also keep the
    unmatched (e.g. a future net-new-in-market view)."""
    index = build_index(scoring_repo, discovery_repo)
    matched = 0
    for c in contact_rows:
        m = index.match(company=c.get("company"), domain=c.get("email_domain"),
                        email=c.get("email"))
        if m:
            matched += 1
            c["account_id"], c["match_tier"], c["matched_lists"] = (
                m.account_id, m.tier, list(m.lists))
    account_by_contact = {c["external_id"]: c.get("account_id") for c in contact_rows}
    for e in event_rows:
        e["account_id"] = account_by_contact.get(e["contact_ext"])
    if not persist_unmatched:
        contact_rows = [c for c in contact_rows if c.get("account_id")]
        event_rows = [e for e in event_rows if e.get("account_id")]
    for c in contact_rows:
        engagement_repo.upsert_contact(c)
    new_events = sum(1 for e in event_rows if engagement_repo.add_event(e))
    return matched, new_events


async def run_sync(*, engagement_repo, scoring_repo, discovery_repo,
                   client: ReplyioClient | None = None, days: int = 30,
                   max_contacts: int | None = None, now: str | None = None) -> dict:
    """Pull Reply.io (read-only), normalize, cross to accounts, store. Returns stats.

    `max_contacts` caps the roster pull (None = all; 0 = skip the roster and derive
    identity from the activity rows alone — fast). Crossing + storage are always run.
    """
    client = client or ReplyioClient()
    now = now or datetime.now(UTC).isoformat()
    engagement_repo.set_sync_state(SOURCE, status="running")
    try:
        frm, to = default_window(days)
        activity = [r async for r in client.iter_email_activity(date_from=frm, date_to=to)]
        if len(activity) < _MIN_ACTIVITY_ROWS and days < 60:
            days = 60
            frm, to = default_window(days)
            activity = [r async for r in client.iter_email_activity(date_from=frm, date_to=to)]

        contacts: list[dict] = []
        if max_contacts != 0:
            async for c in client.iter_contacts():
                contacts.append(c)
                if max_contacts and len(contacts) >= max_contacts:
                    break

        wf, wt = frm.date().isoformat(), to.date().isoformat()
        # ELT raw landing: keep the activity we transform from, for replay/audit.
        engagement_repo.land_raw("email_activity",
                                 {"from": wf, "to": wt, "items": activity})
        if contacts:
            engagement_repo.land_raw("contact", {"count": len(contacts)})

        contact_rows, event_rows = ingest_mod.ingest(contacts, activity, now=now)

        matched, new_events = cross_and_persist(
            engagement_repo=engagement_repo, scoring_repo=scoring_repo,
            discovery_repo=discovery_repo, contact_rows=contact_rows,
            event_rows=event_rows)

        stats = {
            "window_days": days, "activity_rows": len(activity),
            "contacts": len(contact_rows), "events": len(event_rows),
            "new_events": new_events, "matched_contacts": matched,
            "unresolved_contacts": len(contact_rows) - matched,
        }
        engagement_repo.set_sync_state(SOURCE, status="success",
                                       window_from=wf, window_to=wt, stats=stats)
        logger.info("engagement sync ok: %s", stats)
        return stats
    except Exception as exc:  # noqa: BLE001 — record failure; never crash the caller loop
        logger.exception("engagement sync failed")
        engagement_repo.set_sync_state(SOURCE, status="failed", error=str(exc)[:300])
        raise


def run_podcast_sync(*, engagement_repo, scoring_repo, discovery_repo,
                     rows: list[dict], now: str | None = None) -> dict:
    """Ingest Podcast Lead Status rows -> cross -> store. Idempotent; records state.

    `rows` are header-keyed dicts (podcast.load_csv output). The caller pulls the
    sheet snapshot read-only; this never writes back to the sheet. Same source-
    agnostic cross + store path as the Reply.io sync, so podcast heat rolls up into
    the same accounts.
    """
    now = now or datetime.now(UTC).isoformat()
    engagement_repo.set_sync_state(PODCAST_SOURCE, status="running")
    try:
        # ELT raw landing: keep the rows we transform from, for replay/audit.
        engagement_repo.land_raw("podcast_rows", {"count": len(rows), "rows": rows},
                                 source=PODCAST_SOURCE)
        contact_rows, event_rows = podcast_mod.parse_rows(rows, now=now)
        matched, new_events = cross_and_persist(
            engagement_repo=engagement_repo, scoring_repo=scoring_repo,
            discovery_repo=discovery_repo, contact_rows=contact_rows,
            event_rows=event_rows)
        stats = {
            "rows": len(rows), "contacts": len(contact_rows),
            "events": len(event_rows), "new_events": new_events,
            "matched_contacts": matched,
            "unresolved_contacts": len(contact_rows) - matched,
        }
        engagement_repo.set_sync_state(PODCAST_SOURCE, status="success", stats=stats)
        logger.info("podcast sync ok: %s", stats)
        return stats
    except Exception as exc:  # noqa: BLE001 — record failure; never crash the caller loop
        logger.exception("podcast sync failed")
        engagement_repo.set_sync_state(PODCAST_SOURCE, status="failed", error=str(exc)[:300])
        raise


def run_sfdc_sync(*, engagement_repo, scoring_repo, discovery_repo, client=None,
                  since: str = "2026-01-01", now: str | None = None) -> dict:
    """Pull Salesforce (read-only) LEAD engagement -> cross -> store. Idempotent;
    records state. Same source-agnostic cross + store path as the other sources, so
    SFDC heat rolls up into the same accounts.

    Two lead signals, both created on/after `since` (YYYY-MM-DD; default the 2026
    cohort): the org's **High Intent Leads** (inbound contact/sales forms ≈ BOFU) and
    **tradeshow-Qualified** leads (a meeting booked at a tradeshow). Booked meetings +
    opportunities are implemented (sfdc.parse) but not yet wired in here. `client` is
    a SalesforceClient (injected in tests); created from .env otherwise.
    """
    if client is None:
        from auto_search.engagement.sfdc_client import SalesforceClient
        client = SalesforceClient()
    now = now or datetime.now(UTC).isoformat()
    engagement_repo.set_sync_state(SFDC_SOURCE, status="running")
    try:
        hi_leads = list(client.iter_high_intent_leads(since=since))
        ts_leads = list(client.iter_tradeshow_leads(since=since))
        lo_leads = list(client.iter_low_intent_leads(since=since))
        # ELT raw landing: keep what we transform from, for replay/audit.
        engagement_repo.land_raw(
            "sfdc_leads", {"high_intent": len(hi_leads), "tradeshow": len(ts_leads),
                           "low_intent": len(lo_leads)}, source=SFDC_SOURCE)

        c1, e1 = sfdc_mod.parse_leads(hi_leads, kind="high_intent_lead",
                                      channel="form", campaign_field="LeadSource", now=now)
        c2, e2 = sfdc_mod.parse_leads(ts_leads, kind="tradeshow", channel="event",
                                      campaign_field="Tradeshow__c", now=now)
        c3, e3 = sfdc_mod.parse_leads(lo_leads, kind="low_intent_lead",
                                      channel="content", campaign_field="LeadSource", now=now)
        # one contact per Lead id (a lead in multiple sets dedups); events keep each kind
        contacts_by_id = {c["external_id"]: c for c in (c1 + c2 + c3)}
        contact_rows, event_rows = list(contacts_by_id.values()), e1 + e2 + e3
        matched, new_events = cross_and_persist(
            engagement_repo=engagement_repo, scoring_repo=scoring_repo,
            discovery_repo=discovery_repo, contact_rows=contact_rows,
            event_rows=event_rows, persist_unmatched=False)
        stats = {
            "high_intent_leads": len(hi_leads), "tradeshow_leads": len(ts_leads),
            "low_intent_leads": len(lo_leads),
            "contacts": len(contact_rows), "events": len(event_rows),
            "new_events": new_events, "matched_contacts": matched,
            "unresolved_contacts": len(contact_rows) - matched,
        }
        engagement_repo.set_sync_state(SFDC_SOURCE, status="success", stats=stats)
        logger.info("sfdc sync ok: %s", stats)
        return stats
    except Exception as exc:  # noqa: BLE001 — record failure; never crash the caller loop
        logger.exception("sfdc sync failed")
        engagement_repo.set_sync_state(SFDC_SOURCE, status="failed", error=str(exc)[:300])
        raise
