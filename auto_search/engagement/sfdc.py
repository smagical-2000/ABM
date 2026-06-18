"""SFDC ingest — Salesforce records -> engagement contacts + events. PURE (no I/O).

Active sources:
- **high-intent inbound leads** (`parse_leads`) — the org's High Intent Leads
  definition (contact/sales-form LeadSources). Contact-level: one contact + one
  'high_intent_lead' event (≈ BOFU, 10) per Lead id, crossed by email domain / company.
  Reused for tradeshow-Qualified + TOFU leads via the `kind`/`channel` args.
- **Sales Accepted Opportunities** (`parse_sao`) — Opportunity.Qualified_Meeting__c
  = true. Account-level: one 'sales_accepted_opportunity' event (≈ BOFU, 10) per
  account, crossed by Account domain / company.

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
                now: str | None = None) -> tuple[list[dict], list[dict]]:
    """Map SFDC leads to (contact_rows, event_rows). PURE.

    Contact-level (each lead is a person): one contact + one event per Lead, keyed by
    the Lead id, so a re-sync is idempotent and two people from one company both count.
    Reused for both lead signals — `kind`/`channel`/`campaign_field` distinguish them:
    high-intent inbound (`form`/`high_intent_lead`, campaign = LeadSource) and
    tradeshow-qualified meetings (`event`/`tradeshow`, campaign = Tradeshow__c).
    Crossing by email domain / company is applied later by cross.py.
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
        domain = (clean_domain(ld.get("BN_Email_Domain__c"))
                  or _email_domain(email) or _website_domain(ld.get("Website")))
        occurred = _dt(ld.get("CreatedDate")) or now
        contact_rows.append({
            "source": SOURCE, "external_id": lid, "email": email,
            "email_domain": domain, "company": company,
            "company_key": normalize_company_name(company or ""),
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
    return contact_rows, event_rows


def parse_sao(opportunities: list[dict], *, now: str | None = None
              ) -> tuple[list[dict], list[dict]]:
    """Map Sales Accepted Opportunities -> (contact_rows, event_rows). PURE.

    Account-level (an SAO is a deal on an Account; the integration user can't read a
    person on it): one contact + one 'sales_accepted_opportunity' event per account,
    deduped to the most-recent date — so an account with several SAOs scores 10 once,
    not 10×N (the one-touch-per-account×kind rule). Kind weight = 10 (≈ BOFU). The
    raw payload keeps every SAO's stage/amount/won + ids for the audit trail.
    Crossing by Account domain / company is applied later by cross.py.
    """
    now = now or datetime.now(UTC).isoformat()
    accounts: dict[str, dict] = {}
    for o in opportunities:
        key, company, domain = _account_identity(o, name_from_subject=False)
        if not key:
            continue
        acc = _slot(accounts, key, company, domain)
        occurred = _dt(o.get("CreatedDate")) or now
        _record(acc["opp"], occurred, _sid(o.get("Id")), o.get("Name"),
                stage=o.get("StageName"), is_won=bool(o.get("IsWon")),
                amount=o.get("Amount"), sql_create_date=o.get("SQL_Create_Date__c"),
                qual_call_date=o.get("Qualification_Call_Date__c"))

    contact_rows: list[dict] = []
    event_rows: list[dict] = []
    for key, acc in accounts.items():
        contact_rows.append({
            "source": SOURCE, "external_id": key, "email": None,
            "email_domain": acc["domain"], "company": acc["company"],
            "company_key": normalize_company_name(acc["company"] or ""),
            "title": None, "meeting_booked": True,   # SAO implies a qualified meeting
            "opted_out": False,
        })
        event_rows.append(_event(key, "crm", "sales_accepted_opportunity",
                                 acc["company"], acc["opp"], now))
    return contact_rows, event_rows


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
        acc = _slot(accounts, key, company, domain)
        occurred = (_dt(m.get("StartDateTime")) or _dt(m.get("ActivityDateTime"))
                    or _dt(m.get("CreatedDate")) or now)
        _record(acc["meeting"], occurred, _sid(m.get("Id")), m.get("Subject"))

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
