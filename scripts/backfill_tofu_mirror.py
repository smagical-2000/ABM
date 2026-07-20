"""One-command activation for the TOFU tracking mirror (Galyna, 2026-07-08).

The mirror is a second Airtable base ("TOFU Leads by ABM") that receives a copy
of every captured LinkedIn TOFU lead, so the team can audit that the capture
workflow misses nothing. The runner dual-writes new leads once
AIRTABLE_TOFU_MIRROR_BASE_ID is set; THIS script does the one-time setup:

  1. Ensures the mirror TABLE exists — created via the Airtable meta API by
     cloning the primary table's schema (same columns) plus a "Synced At"
     dateTime column. Idempotent: an existing table is reused.
  2. Backfills FUNNEL LEADS ONLY from the primary "LinkedIn <> Airtable"
     table (upsert on Email, else LinkedIn URL), stamping Synced At. A funnel
     lead = a row matching a Salesforce TOFU-campaign Lead OR a lead the ABM
     runner captured. The primary table also holds ~1,600 Clay bulk-dump rows
     that never became leads — Galyna's table must NOT include those
     (2026-07-08: the first backfill copied everything; she expected ~40, saw
     1,677; cleaned to 41. This filter keeps that mistake unrepeatable).

Usage:
    python scripts/backfill_tofu_mirror.py            # dry-run: reports counts
    python scripts/backfill_tofu_mirror.py --apply    # create table + write

Needs: AIRTABLE_API_KEY with access to BOTH bases (data read/write + schema
write on the mirror base), AIRTABLE_BASE_ID / AIRTABLE_LINKEDIN_TABLE (primary),
AIRTABLE_TOFU_MIRROR_BASE_ID (+ optional AIRTABLE_TOFU_MIRROR_TABLE name).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv()   # no override: an operator-exported DATABASE_URL must win

_META = "https://api.airtable.com/v0/meta/bases"
# Field types the meta API can create as-is; anything else lands as text.
_CLONEABLE = {"singleLineText", "multilineText", "url", "email", "phoneNumber",
              "number", "checkbox", "dateTime", "date", "singleSelect", "multipleSelects"}


def _hdr() -> dict:
    return {"Authorization": f"Bearer {os.environ['AIRTABLE_API_KEY']}",
            "Content-Type": "application/json"}


def _clone_field(f: dict) -> dict:
    """A creatable field spec from a primary-table field. Options are copied
    when present; unsupported types degrade to plain text (data still lands
    because writes use typecast)."""
    if f["type"] not in _CLONEABLE:
        return {"name": f["name"], "type": "singleLineText"}
    out = {"name": f["name"], "type": f["type"]}
    if f.get("options"):
        out["options"] = f["options"]
    return out


def ensure_mirror_table(primary_base: str, primary_table: str,
                        mirror_base: str, mirror_name: str, *, apply: bool) -> str | None:
    """Return the mirror table id (creating it if needed under --apply)."""
    with httpx.Client(timeout=30) as c:
        prim = c.get(f"{_META}/{primary_base}/tables", headers=_hdr())
        prim.raise_for_status()
        src = next(t for t in prim.json()["tables"]
                   if t["name"] == primary_table or t["id"] == primary_table)
        mirr = c.get(f"{_META}/{mirror_base}/tables", headers=_hdr())
        mirr.raise_for_status()
        existing = next((t for t in mirr.json()["tables"]
                         if t["name"] == mirror_name or t["id"] == mirror_name), None)
        if existing:
            print(f"[mirror] table exists: {existing['name']} ({existing['id']})")
            return existing["id"]
        # Clone every primary column (incl. "ABM Match" — the runner dual-writes
        # it since capture-all, 2026-07-08) except Synced At, added fresh below.
        fields = [_clone_field(f) for f in src["fields"] if f["name"] != "Synced At"]
        fields.append({"name": "Synced At", "type": "dateTime",
                       "options": {"timeZone": "utc",
                                   "dateFormat": {"name": "iso"},
                                   "timeFormat": {"name": "24hour"}}})
        if not apply:
            print(f"[mirror] would CREATE table '{mirror_name}' with "
                  f"{len(fields)} columns (dry-run)")
            return None
        r = c.post(f"{_META}/{mirror_base}/tables", headers=_hdr(),
                   json={"name": mirror_name, "fields": fields})
        r.raise_for_status()
        tid = r.json()["id"]
        print(f"[mirror] created table '{mirror_name}' ({tid})")
        return tid


def _funnel_lead_keys() -> tuple[set[str], set[str]]:
    """The identities that belong in Galyna's table: Salesforce TOFU-campaign
    Leads (read-only SOQL) ∪ leads the ABM runner captured (engagement store).
    Returns (emails, linkedin_urls), all lowercased."""
    from auto_search.db.engagement_repository import get_engagement_repository
    from auto_search.engagement.sfdc_client import SalesforceClient

    emails: set[str] = set()
    try:
        # TWO lead shapes count (2026-07-08, Lynn Osgood case): the older Zap
        # stamped LeadSource='TOFU Engagement Campaign'; the current Zap stamps
        # NOTHING (LeadSource null) — so also take Alykhan-created null-source
        # leads. Safe from over-matching: only emails that ALSO exist as rows
        # in the primary table can enter the tracking table (the caller filters
        # primary rows by this set), so a manual unrelated lead changes nothing.
        for lead in SalesforceClient().query(
                "SELECT Email FROM Lead WHERE LeadSource = 'TOFU Engagement Campaign' "
                "OR (LeadSource = null AND CreatedBy.Name = 'Alykhan Jina')"):
            if lead.get("Email"):
                emails.add(lead["Email"].strip().lower())
    except Exception as e:  # noqa: BLE001 — SFDC down: proceed with runner set only
        print(f"[mirror] WARNING: SFDC lead pull failed ({e}) — runner captures only")
    member_ids: set[str] = set()
    repo = get_engagement_repository()
    runner_contacts = 0
    for c in repo.contacts():
        ext = c.get("external_id") or ""
        if ext.startswith("linkedin:"):
            runner_contacts += 1
            if c.get("email"):
                emails.add(c["email"].strip().lower())
            # phone-only leads: the LinkedIn member id after the prefix is the
            # stable key; the Airtable row's LinkedIn URL contains it.
            member_ids.add(ext.split(":", 1)[1].strip().lower())
    if runner_contacts == 0:
        print("[mirror] WARNING: engagement store returned NO runner captures — "
              "if running locally, DATABASE_URL probably points at the wrong "
              "database; phone-only leads would be missed.")
    return emails, member_ids


async def backfill(mirror_table_id: str | None, *, apply: bool) -> None:
    from auto_search.engagement.airtable_client import AirtableClient

    primary = AirtableClient()
    rows = await primary.records()
    print(f"[mirror] primary rows: {len(rows)}")
    keep_emails, keep_urls = _funnel_lead_keys()
    print(f"[mirror] funnel-lead keys: {len(keep_emails)} emails, {len(keep_urls)} member ids")

    def _is_funnel_lead(r: dict) -> bool:
        f = r["fields"]
        em = (f.get("Email") or "").strip().lower()
        url = (f.get("LinkedIn URL") or "").strip().lower()
        return ((em != "" and em in keep_emails)
                or (url != "" and any(mid and mid in url for mid in keep_urls)))

    rows = [r for r in rows if _is_funnel_lead(r)]
    if not apply:
        print(f"[mirror] would backfill {len(rows)} funnel-lead rows "
              "(Clay bulk rows excluded). Dry-run.")
        return

    # Divergence detector (Lynn Osgood, 2026-07-08): a funnel lead that is NOT
    # a runner capture arrived via the CLAY path. Since capture-all shipped
    # (MAR2-19, decided 2026-07-08: every reactor is captured, ABM or not),
    # new ones are unexpected — likely a reactor the actor's ~20-visible-
    # reactions cap hid from us. Alert so the team hears it from us first.
    def _is_runner_capture(r: dict) -> bool:
        url = (r["fields"].get("LinkedIn URL") or "").strip().lower()
        return url != "" and any(mid and mid in url for mid in keep_urls)

    mirror_probe = AirtableClient(base_id=os.environ["AIRTABLE_TOFU_MIRROR_BASE_ID"],
                                  table=mirror_table_id
                                  or os.getenv("AIRTABLE_TOFU_MIRROR_TABLE", "TOFU Leads by ABM"))
    existing_keys = set()
    for r in await mirror_probe.records():
        f = r["fields"]
        if f.get("Email"):
            existing_keys.add(f["Email"].strip().lower())
        if f.get("LinkedIn URL"):
            existing_keys.add(f["LinkedIn URL"].strip().lower())

    def _row_key(r: dict) -> str:
        f = r["fields"]
        return (f.get("Email") or f.get("LinkedIn URL") or "").strip().lower()

    clay_path_new = [r for r in rows
                     if _row_key(r) not in existing_keys and not _is_runner_capture(r)]
    mirror = AirtableClient(base_id=os.environ["AIRTABLE_TOFU_MIRROR_BASE_ID"],
                            table=mirror_table_id
                            or os.getenv("AIRTABLE_TOFU_MIRROR_TABLE", "TOFU Leads by ABM"))
    now = datetime.now(UTC).isoformat()
    ok = failed = unkeyed = 0
    for r in rows:
        fields = {k: v for k, v in r["fields"].items() if v not in (None, "")}
        fields["Synced At"] = now
        try:
            if fields.get("Email"):
                await mirror.upsert(fields, merge_on=["Email"])
            elif fields.get("LinkedIn URL"):
                await mirror.upsert(fields, merge_on=["LinkedIn URL"])
            else:
                # No merge key -> an upsert can't find it and a create would
                # DUPLICATE a row that was already copied. Skip + report.
                unkeyed += 1
                continue
            ok += 1
        except Exception as e:  # noqa: BLE001 — keep going; report at the end
            failed += 1
            print(f"[mirror] FAILED row {r['id']}: {e}", file=sys.stderr)
    print(f"[mirror] backfill done: {ok} written, {failed} failed, "
          f"{unkeyed} unkeyed skipped, of {len(rows)}")
    if clay_path_new:
        from auto_search.ops import alerts
        names = "; ".join(
            f"{r['fields'].get('First Name', '')} {r['fields'].get('Last Name', '')}".strip()
            + (f" ({r['fields'].get('Company Name')})" if r['fields'].get('Company Name') else "")
            for r in clay_path_new[:10])
        alerts.post_ops_alert(
            kind="tofu-clay-path", severity="warning", service="discovery-cron",
            title=f"{len(clay_path_new)} lead(s) reached the funnel via the Clay path, "
                  "not captured by the ABM runner",
            detail=f"{names}. Capture-all is ON (2026-07-08), so these were likely "
                   "hidden from the reactions actor (~20 visible per post) or "
                   "arrived outside the scan window. Added to the tracking table now.")
        print(f"[mirror] clay-path alert posted for {len(clay_path_new)} lead(s)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Create + backfill the TOFU tracking mirror")
    ap.add_argument("--apply", action="store_true", help="actually create/write")
    args = ap.parse_args()
    mirror_base = os.getenv("AIRTABLE_TOFU_MIRROR_BASE_ID")
    if not mirror_base:
        print("AIRTABLE_TOFU_MIRROR_BASE_ID not set — add the mirror base id first.")
        return 1
    tid = ensure_mirror_table(os.environ["AIRTABLE_BASE_ID"],
                              os.environ["AIRTABLE_LINKEDIN_TABLE"], mirror_base,
                              os.getenv("AIRTABLE_TOFU_MIRROR_TABLE", "TOFU Leads by ABM"),
                              apply=args.apply)
    asyncio.run(backfill(tid, apply=args.apply))
    return 0


if __name__ == "__main__":
    sys.exit(main())
