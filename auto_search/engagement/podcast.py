"""Podcast leads -> normalized engagement contacts + events. PURE ingest + CSV loader.

The "Podcast Lead Status" sheet (manually ICP-qualified inbound podcast leads) is a
second engagement source feeding the same pipeline as Reply.io. We track ICP
Yes/Maybe (skip No / blank / junk), key identity on the work email, and emit one
`podcast:podcast_lead:<email>` event per lead — 4 pts, the canonical Podcast /
listen-download weight. Crossing to a scored/ABM account happens later at sync time
(cross.py); unmatched leads surface in the Resolve list (no net-new accounts in v1).

`load_csv()` turns a sheet CSV export into row dicts; `parse_rows()` is pure and maps
those to (contact_rows, event_rows) ready for engagement_repository.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime

from auto_search.engagement import scoring
from auto_search.normalize import clean_domain, normalize_company_name

SOURCE = "podcast"
CHANNEL = "podcast"
KIND = "podcast_lead"
_ICP_RANK = {"Yes": 1, "Maybe": 0}


def load_csv(text: str) -> list[dict]:
    """Parse a Podcast Lead Status CSV export into header-keyed row dicts."""
    return list(csv.DictReader(io.StringIO(text)))


def parse_rows(rows: list[dict], *, now: str | None = None
               ) -> tuple[list[dict], list[dict]]:
    """Map sheet rows -> (contact_rows, event_rows). PURE, no I/O.

    Keeps ICP Yes/Maybe rows that carry a work email; dedups by email (a person can
    submit twice) preferring ICP Yes over Maybe, then the most recent submit.
    """
    now = now or datetime.now(UTC).isoformat()
    best: dict[str, dict] = {}
    for r in rows:
        icp = _norm_icp(_get(r, "ICP"))
        if icp not in ("Yes", "Maybe"):
            continue
        email = _email(_get(r, "Work Email"))
        if not email:
            continue
        cand = {
            "email": email, "icp": icp,
            "company": _get(r, "Account Name") or None,
            "campaign": _get(r, "Lead Form") or _get(r, "UTM_campaign") or None,
            "occurred": _parse_date(_get(r, "Submit Date")) or now,
            "utm_source": _get(r, "UTM_source") or None,
            "utm_campaign": _get(r, "UTM_campaign") or None,
        }
        prev = best.get(email)
        if prev is None or _better(cand, prev):
            best[email] = cand

    contact_rows: list[dict] = []
    event_rows: list[dict] = []
    for email, c in sorted(best.items()):
        company = c["company"]
        contact_rows.append({
            "source": SOURCE, "external_id": email, "email": email,
            "email_domain": _domain(email),
            "company": company, "company_key": normalize_company_name(company or ""),
            "title": None, "meeting_booked": False, "opted_out": False,
            "sent": 0, "delivered": 0, "opened": 0, "clicked": 0,
            "replied": 0, "bounced": 0,
        })
        event_rows.append({
            "source": SOURCE, "external_id": f"{CHANNEL}:{KIND}:{email}",
            "channel": CHANNEL, "kind": KIND, "points": scoring.points_for(KIND),
            "contact_ext": email, "company": company, "campaign": c["campaign"],
            "occurred_at": c["occurred"],
            "raw": {"icp": c["icp"], "utm_source": c["utm_source"],
                    "utm_campaign": c["utm_campaign"]},
        })
    return contact_rows, event_rows


# ── helpers ────────────────────────────────────────────────────────────


def _better(cand: dict, prev: dict) -> bool:
    """Prefer ICP Yes over Maybe; tie-break on the more recent submit."""
    if _ICP_RANK[cand["icp"]] != _ICP_RANK[prev["icp"]]:
        return _ICP_RANK[cand["icp"]] > _ICP_RANK[prev["icp"]]
    return (cand["occurred"] or "") > (prev["occurred"] or "")


def _get(row: dict, key: str) -> str:
    return str(row.get(key) or "").strip()


def _norm_icp(v: str) -> str:
    v = v.lstrip("-").strip().lower()
    if v.startswith("yes"):
        return "Yes"
    if v.startswith("maybe"):
        return "Maybe"
    if v.startswith("no"):
        return "No"
    return ""


def _email(v: str) -> str:
    e = v.strip().lower()
    return e if "@" in e and "." in e.rsplit("@", 1)[-1] else ""


def _domain(email: str) -> str | None:
    return clean_domain(email.rsplit("@", 1)[-1]) if "@" in email else None


_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y %H:%M:%S",
                 "%m/%d/%Y", "%m/%d/%y")


def _parse_date(v: str) -> str | None:
    v = v.strip()
    if not v:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(v, fmt).replace(tzinfo=UTC).isoformat()
        except ValueError:
            continue
    return None
