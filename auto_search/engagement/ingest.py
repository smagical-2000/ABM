"""Ingest — Reply.io rows -> normalized engagement contacts + events. PURE (no I/O).

Reply.io's /reporting/emails gives per-contact-per-send booleans; we aggregate to
per-contact send-stat COUNTS (for open/reply rates) and emit one MEANINGFUL event
per contact x kind (click / reply / meeting_booked), with points from the scorer.
The "channel:kind:contactId" external_id makes re-ingest idempotent and stops a
long contact list inflating a score.

Crossing (account_id) is applied later by cross.py at sync time, so here events +
contacts are left unmatched. Hand this the rows from replyio_client; get back
dicts ready for engagement_repository.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from auto_search.engagement import scoring
from auto_search.normalize import clean_domain, normalize_company_name

SOURCE = "replyio"
CHANNEL = "email"


def ingest(contacts: list[dict], activity: list[dict], *, now: str | None = None
           ) -> tuple[list[dict], list[dict]]:
    """Map Reply.io contacts + email-activity rows to (contact_rows, event_rows).

    `now` is the fallback timestamp for a meaningful touch that has no source date
    (e.g. a booked meeting carried on the contact, not in the activity feed);
    passed in for deterministic tests.
    """
    now = now or datetime.now(UTC).isoformat()

    # 1) aggregate per-send activity rows down to per-contact counts + latest dates.
    agg: dict[str, dict] = defaultdict(_blank_agg)
    for row in activity:
        cid = _cid(row.get("contactId"))
        if not cid:
            continue
        a = agg[cid]
        a["sent"] += 1
        for flag, key in (("isDelivered", "delivered"), ("isOpened", "opened"),
                          ("isClicked", "clicked"), ("isReplied", "replied"),
                          ("isBounced", "bounced")):
            if row.get(flag):
                a[key] += 1
        a["email"] = a["email"] or row.get("email")
        a["company"] = a["company"] or row.get("company")
        a["campaign"] = row.get("sequenceName") or a["campaign"]
        dt = row.get("deliveryDate")
        a["last_any"] = _max_iso(a["last_any"], dt)
        if row.get("isClicked"):
            a["last_click"] = _max_iso(a["last_click"], dt)
        if row.get("isReplied"):
            a["last_reply"] = _max_iso(a["last_reply"], dt)

    # 2) index the contact roster by id (same id space as activity's contactId).
    by_id = {cid: c for c in contacts if (cid := _cid(c.get("id")))}

    # Engaged universe = contacts emailed in the window (have activity) OR carrying
    # a booked meeting. Roster contacts with neither are NOT engaged — skip them so
    # the Accounts list isn't flooded with thousands of zero-engagement rows.
    meeting_ids = {cid for cid, c in by_id.items()
                   if c.get("meetingStatus") == "meetingBooked"}

    contact_rows: list[dict] = []
    event_rows: list[dict] = []
    for cid in sorted(set(agg) | meeting_ids):
        c = by_id.get(cid, {})
        a = agg.get(cid) or _blank_agg()
        email = c.get("email") or a["email"]
        company = c.get("company") or a["company"]
        meeting = c.get("meetingStatus") == "meetingBooked"
        campaign = a["campaign"]

        contact_rows.append({
            "source": SOURCE, "external_id": cid, "email": email,
            "email_domain": _domain(email, c.get("domain")),
            "company": company, "company_key": normalize_company_name(company or ""),
            "title": c.get("title"), "meeting_booked": meeting,
            "opted_out": bool(c.get("isOptedOut")),
            "sent": a["sent"], "delivered": a["delivered"], "opened": a["opened"],
            "clicked": a["clicked"], "replied": a["replied"], "bounced": a["bounced"],
        })

        if a["clicked"]:
            event_rows.append(_event(cid, "click", a["last_click"] or a["last_any"] or now,
                                     company, campaign))
        if a["replied"]:
            event_rows.append(_event(cid, "reply", a["last_reply"] or a["last_any"] or now,
                                     company, campaign))
        if meeting:
            occurred = a["last_any"] or c.get("lastModifiedAt") or c.get("createdAt") or now
            event_rows.append(_event(cid, "meeting_booked", occurred, company, campaign))

    return contact_rows, event_rows


# ── helpers ────────────────────────────────────────────────────────────


def _event(cid: str, kind: str, occurred: str, company: str | None,
           campaign: str | None) -> dict:
    return {"source": SOURCE, "external_id": f"{CHANNEL}:{kind}:{cid}", "channel": CHANNEL,
            "kind": kind, "points": scoring.points_for(kind), "contact_ext": cid,
            "company": company, "campaign": campaign, "occurred_at": occurred, "raw": {}}


def _blank_agg() -> dict:
    return {"sent": 0, "delivered": 0, "opened": 0, "clicked": 0, "replied": 0,
            "bounced": 0, "email": None, "company": None, "campaign": None,
            "last_any": None, "last_click": None, "last_reply": None}


def _cid(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _domain(email: str | None, fallback: str | None = None) -> str | None:
    e = (email or "").strip().lower()
    if "@" in e:
        d = clean_domain(e.rsplit("@", 1)[-1])
        if d:
            return d
    return clean_domain(fallback)


def _max_iso(a: str | None, b: str | None) -> str | None:
    if not a:
        return b
    if not b:
        return a
    return a if a >= b else b
