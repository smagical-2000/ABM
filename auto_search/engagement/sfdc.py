"""SFDC ingest — Salesforce records -> engagement contacts + events. PURE (no I/O).

Active sources:
- **high-intent inbound leads** (`parse_leads`) — the org's High Intent Leads
  definition (contact/sales-form LeadSources). Contact-level: one contact + one
  'high_intent_lead' event (≈ BOFU, 10) per Lead id, crossed by email domain / company.
  Reused for tradeshow-Qualified + TOFU leads via the `kind`/`channel` args.

(SAO was retired in the 2026-06 review — replaced by `meeting_booked`; see
`scoring.DEPRECATED_KINDS`. The parser was deleted with it.)

Also implemented but not yet wired into the live sync — booked meetings + open/won
opportunities (`parse`). Tasks are excluded (99% outbound rep dials = activity, not
intent). Account-level parsers cross at the **account** level (Who.Email isn't
readable for the integration user) and emit at most one event per account x kind, so
an account with 20 auto-logged meetings scores 10, not 200.

Crossing (account_id) is applied later by cross.py at sync time. Hand these the raw
records from sfdc_client; get back dicts ready for cross_and_persist.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from auto_search.engagement import scoring
from auto_search.normalize import clean_domain, normalize_company_name

SOURCE = "sfdc"


def parse_leads(leads: list[dict], *, kind: str = "high_intent_lead",
                channel: str = "form", campaign_field: str = "LeadSource",
                declared: bool = False,
                now: str | None = None) -> tuple[list[dict], list[dict]]:
    """Map SFDC leads to (contact_rows, event_rows). PURE.

    Contact-level (each lead is a person): one contact + one event per Lead, keyed by
    the Lead id, so a re-sync is idempotent and two people from one company both count.
    Reused for both lead signals — `kind`/`channel`/`campaign_field` distinguish them:
    high-intent inbound (`form`/`high_intent_lead`, campaign = LeadSource) and
    tradeshow-qualified meetings (`event`/`tradeshow`, campaign = Tradeshow__c).
    Crossing by email domain / company is applied later by cross.py.

    `declared=True` (BOFU-class sources, MAR2-50) stamps `company_declared` on the
    contact rows: the human typed the company on our own form, so the cross may
    bind the name match past the domain-contradiction veto ('name+bofu'). The
    flag is in-memory routing only — the repo's contact whitelist drops it.
    """
    now = now or datetime.now(UTC).isoformat()
    contact_rows: list[dict] = []
    event_rows: list[dict] = []
    points = scoring.points_for(kind)
    seen: set[str] = set()
    for ld in leads:
        lid = _sid(ld.get("Id"))
        if not lid or lid in seen:
            continue
        seen.add(lid)
        email = (ld.get("Email") or "").strip() or None
        company = ld.get("Company")
        name = (f"{ld.get('FirstName') or ''} {ld.get('LastName') or ''}".strip()
                or None)
        domain = (clean_domain(ld.get("BN_Email_Domain__c"))
                  or _email_domain(email) or _website_domain(ld.get("Website")))
        occurred = _dt(ld.get("CreatedDate")) or now
        contact_rows.append({
            "source": SOURCE, "external_id": lid, "email": email,
            "email_domain": domain, "company": company,
            "company_key": normalize_company_name(company or ""),
            "name": name, "company_declared": declared,
            "title": ld.get("Title"), "meeting_booked": kind == "tradeshow",
            "opted_out": False,
        })
        event_rows.append({
            "source": SOURCE, "external_id": f"{channel}:{kind}:{lid}",
            "channel": channel, "kind": kind, "points": points, "contact_ext": lid,
            "company": company, "campaign": ld.get(campaign_field),
            "occurred_at": occurred,
            "raw": {"lead_source": ld.get("LeadSource"), "status": ld.get("Status"),
                    "tradeshow": ld.get("Tradeshow__c"), "rating": ld.get("Rating"),
                    "mql": bool(ld.get("MQL__c")),
                    "seats_requested": ld.get("Seats_Requested__c"),
                    "in_healthcare": ld.get("In_Healthcare__c"),
                    "primary_purpose": ld.get("Primary_Purpose__c"),
                    "employee_range": ld.get("Employee_Range__c"),
                    "is_converted": bool(ld.get("IsConverted"))},
        })
    return _collapse_same_day_resubmits(contact_rows, event_rows)


def _collapse_same_day_resubmits(contact_rows: list[dict], event_rows: list[dict]
                                 ) -> tuple[list[dict], list[dict]]:
    """One form-lead event per (person, day) — the Ascension double-count
    (MAR2-50): one human submitting the form twice in 6 minutes mints two SFDC
    Lead ids, hence two event external_ids and +20 instead of +10. Collapse to
    the OLDEST lead id, which is stable across re-pulls — an insert guard, not
    a migration (already-stored events simply keep re-upserting under the same
    id). Kind is constant within a parse_leads call, so the key is
    (person, occurred date); person = email when present, else normalized
    name+company (the echo filter's rule). A re-engagement on a LATER day keeps
    its own lead id and still counts. PURE, order-independent."""
    by_ext = {c["external_id"]: c for c in contact_rows}
    best: dict[tuple[str, str], dict] = {}
    for ev in event_rows:
        c = by_ext.get(ev.get("contact_ext")) or {}
        person = ((c.get("email") or "").strip().lower()
                  or normalize_company_name(
                      f"{c.get('name') or ''} {c.get('company') or ''}")
                  or str(ev.get("contact_ext")))
        key = (person, str(ev.get("occurred_at") or "")[:10])
        cur = best.get(key)
        if cur is None or _event_order(ev) < _event_order(cur):
            best[key] = ev
    keep = {ev.get("contact_ext") for ev in best.values()}
    return ([c for c in contact_rows if c.get("external_id") in keep],
            [ev for ev in event_rows if ev.get("contact_ext") in keep])


def _event_order(ev: dict) -> tuple[str, str]:
    """Sort key for the same-day collapse: oldest occurred_at wins, lead id
    breaks the tie (SFDC ids grow over time, so lower ≈ earlier)."""
    return (str(ev.get("occurred_at") or ""), str(ev.get("contact_ext") or ""))


TOFU_ECHO_SOURCE = "TOFU Engagement Campaign"


def filter_tofu_echoes(contact_rows: list[dict], event_rows: list[dict], *,
                       captured_emails: set[str],
                       person_key_by_lid: dict[str, str] | None = None
                       ) -> tuple[list[dict], list[dict]]:
    """Drop SFDC low-intent rows that are ECHOES of our own LinkedIn capture. PURE.

    'TOFU Engagement Campaign' leads are created by the Airtable automation from
    people the LinkedIn runner already captured — anyone in `captured_emails`
    already got linkedin_tofu heat at capture time and must not score twice.
    The automation also re-creates a lead when enrichment lands a second email
    (Pamela Mixon, 2026-07-20: two SFDC leads, two emails, one human) — echo
    leads collapse per PERSON to the OLDEST lead, so the canonical external_id
    is stable across re-pulls. `person_key_by_lid` (Lead id -> lowercased
    name+company key, built by the caller from the raw leads) is what lets the
    two-email Pamela case collapse; a lid without a person key falls back to
    its email, so plain same-email dupes still collapse when the map is absent.
    Non-echo rows pass through untouched."""
    emails = {c.get("external_id"): (c.get("email") or "").strip().lower()
              for c in contact_rows}
    captured = {(e or "").strip().lower() for e in captured_emails if e}
    keys = person_key_by_lid or {}
    drop: set[str] = set()
    seen: set[str] = set()
    echoes = [ev for ev in event_rows if (ev.get("campaign") or "") == TOFU_ECHO_SOURCE]
    for ev in sorted(echoes, key=lambda ev: ev.get("occurred_at") or ""):
        lid = ev.get("contact_ext")
        em = emails.get(lid, "")
        pk = keys.get(lid) or em       # person key when known, else the email
        if em and em in captured:      # already scored at capture time
            drop.add(lid)
        elif pk and pk in seen:        # duplicate SFDC lead for one person
            drop.add(lid)
        elif pk:
            seen.add(pk)
    return ([c for c in contact_rows if c.get("external_id") not in drop],
            [ev for ev in event_rows if ev.get("contact_ext") not in drop])


def parse(meetings: list[dict], opportunities: list[dict], *, now: str | None = None
          ) -> tuple[list[dict], list[dict]]:
    """Map SFDC meeting + opportunity records to (contact_rows, event_rows).

    One contact row per resolved account; one meeting event and/or one opportunity
    event per account (deduped, most-recent occurred_at). `now` is the fallback
    timestamp when a record carries no usable date (deterministic in tests).
    """
    now = now or datetime.now(UTC).isoformat()
    accounts: dict[str, dict] = {}

    for m in meetings:
        key, company, domain = _account_identity(m, name_from_subject=True)
        if not key:
            continue
        # INTRO-ONLY (Griffen's definition, ratified by Sunny 2026-07-20): a
        # booked-meeting signal is a NEW introductory meeting — subject carries
        # "Intro"/"Introduction"/"Introductory". Demos, follow-ups, and internal
        # syncs are pipeline motion, not a new-meeting signal (the CCA
        # "Technical Demo" false Hot, 2026-07-20).
        if "intro" not in (m.get("Subject") or "").lower():
            continue
        acc = _slot(accounts, key, company, domain)
        # A booked meeting can be SCHEDULED in the future; its StartDateTime would then be
        # a future date that inflates today's heat, shows a future timeline entry, and
        # trips the send cutoff. The signal (they agreed to meet) happened when the record
        # was created, so use the meeting time only if it is in the past, else the booking
        # date (CreatedDate); never let it exceed today.
        start = _dt(m.get("StartDateTime")) or _dt(m.get("ActivityDateTime"))
        created = _dt(m.get("CreatedDate"))
        occurred = start if (start and start[:10] <= now[:10]) else (created or now)
        if occurred[:10] > now[:10]:
            occurred = now
        _record(acc["meeting"], occurred, _sid(m.get("Id")), m.get("Subject"))
        # Capture the attendee NAME (Who.Name is already in the meeting query) from the
        # most recent meeting, for the drawer's timeline hover. Who.Email isn't queryable
        # here (WhoId is polymorphic Contact/Lead), so display is name-only (AGT-1442).
        who = m.get("Who") or {}
        nm = (who.get("Name") or "").strip()
        if nm and occurred >= (acc["meeting"].get("attendee_at") or ""):
            acc["meeting"]["attendee"] = {"name": nm, "type": who.get("Type")}
            acc["meeting"]["attendee_at"] = occurred

    for o in opportunities:
        key, company, domain = _account_identity(o, name_from_subject=False)
        if not key:
            continue
        acc = _slot(accounts, key, company, domain)
        occurred = _dt(o.get("CreatedDate")) or now
        _record(acc["opp"], occurred, _sid(o.get("Id")), o.get("Name"),
                stage=o.get("StageName"), is_won=bool(o.get("IsWon")),
                amount=o.get("Amount"))

    contact_rows: list[dict] = []
    event_rows: list[dict] = []
    for key, acc in accounts.items():
        contact_rows.append({
            "source": SOURCE, "external_id": key,
            "email": None, "email_domain": acc["domain"],
            "company": acc["company"],
            "company_key": normalize_company_name(acc["company"] or ""),
            "title": None, "meeting_booked": bool(acc["meeting"]["ids"]),
            "opted_out": False,
        })
        if acc["meeting"]["ids"]:
            event_rows.append(_event(key, "meeting", "meeting_booked", acc["company"],
                                     acc["meeting"], now))
        if acc["opp"]["ids"]:
            event_rows.append(_event(key, "crm", "opportunity", acc["company"],
                                     acc["opp"], now))

    return contact_rows, event_rows


# ── helpers ────────────────────────────────────────────────────────────


def _slot(accounts: dict, key: str, company: str | None, domain: str | None) -> dict:
    acc = accounts.get(key)
    if acc is None:
        acc = accounts[key] = {"company": company, "domain": domain,
                               "meeting": _blank_kind(), "opp": _blank_kind()}
    else:
        acc["company"] = acc["company"] or company
        acc["domain"] = acc["domain"] or domain
    return acc


def _blank_kind() -> dict:
    return {"ids": [], "last": None, "subjects": [], "extra": {}}


def _record(slot: dict, occurred: str, rid: str | None, label: str | None, **extra) -> None:
    if rid:
        slot["ids"].append(rid)
    if label:
        slot["subjects"].append(label)
    slot["last"] = _max_iso(slot["last"], occurred)
    for k, v in extra.items():                       # accumulate opp stages/amounts/won
        if v not in (None, ""):
            slot["extra"].setdefault(k, []).append(v)


def _event(account_key: str, channel: str, kind: str, company: str | None,
           slot: dict, now: str) -> dict:
    raw = {"count": len(slot["ids"]), "ids": slot["ids"],
           "subjects": slot["subjects"][:10], **slot["extra"]}
    if slot.get("attendee"):                  # meeting attendee (name) for the hover
        raw["attendee"] = slot["attendee"]
    return {
        "source": SOURCE, "external_id": f"{channel}:{kind}:{account_key}",
        "channel": channel, "kind": kind, "points": scoring.points_for(kind),
        "contact_ext": account_key, "company": company, "campaign": None,
        "occurred_at": slot["last"] or now, "raw": raw,
    }


def _account_identity(rec: dict, *, name_from_subject: bool
                      ) -> tuple[str | None, str | None, str | None]:
    """Resolve (stable account key, company name, domain) for crossing.

    Prefer the SFDC AccountId (stable, unique). Fall back to a normalized-name key
    so account-less Lead meetings still cross by company name. Returns (None, ...)
    when no usable identity exists (skipped — never guessed)."""
    acct = rec.get("Account") or {}
    company = acct.get("Name")
    domain = _website_domain(acct.get("Website"))
    if not company and name_from_subject:
        company = _company_from_subject(rec.get("Subject"))
    account_id = _sid(rec.get("AccountId"))
    if account_id:
        return f"acct:{account_id}", company, domain
    key = normalize_company_name(company or "")
    if key:
        return f"name:{key}", company, domain
    return None, None, None


def _email_domain(email: str | None) -> str | None:
    e = (email or "").strip().lower()
    return clean_domain(e.rsplit("@", 1)[-1]) if "@" in e else None


def _website_domain(website: str | None) -> str | None:
    """SFDC's Account.Website is a full URL ('https://www.acme.com/'); reduce it to
    the bare registrable domain so it matches how scored/ABM domains are stored
    (clean_domain alone keeps the scheme/path/www)."""
    w = (website or "").strip().lower()
    if not w:
        return None
    w = w.split("://", 1)[-1]            # drop scheme
    w = w.split("/", 1)[0].split("?", 1)[0]   # drop path / query
    if w.startswith("www."):
        w = w[4:]
    return clean_domain(w)


def _company_from_subject(subject: str | None) -> str | None:
    """Intro meetings are titled 'Company <> Magical ...' — take the left side as a
    best-effort company name for account-less (Lead) meetings."""
    s = (subject or "").strip()
    if "<>" in s:
        left = s.split("<>", 1)[0].strip()
        return left or None
    return None


def _sid(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _dt(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _max_iso(a: str | None, b: str | None) -> str | None:
    if not a:
        return b
    if not b:
        return a
    return a if a >= b else b
