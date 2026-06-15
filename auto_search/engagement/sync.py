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
from auto_search.engagement.cross import build_index
from auto_search.engagement.replyio_client import ReplyioClient, default_window

logger = logging.getLogger(__name__)

SOURCE = "replyio"
_MIN_ACTIVITY_ROWS = 20      # below this over `days`, widen the window to 60d


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

        # cross each contact to a scored/ABM account; stamp the result onto its events.
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

        for c in contact_rows:
            engagement_repo.upsert_contact(c)
        new_events = sum(1 for e in event_rows if engagement_repo.add_event(e))

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
