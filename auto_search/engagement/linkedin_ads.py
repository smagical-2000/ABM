"""LinkedIn TOFU ad-engagement source — pure config + mapping (no I/O).

People who like (v1) or comment (later) on Magical's sponsored LinkedIn posts are a
TOFU engagement signal. Each post is tagged with a category (the user maintains a
`share_id -> category` CSV); the category drives three things:

  1. the heat KIND + points recorded in the engagement table (linkedin_tofu = 6, TOFU lead),
  2. the Reply.io campaign the contact is pushed into,
  3. the segment we file the account under.

This module is the single source of truth for those mappings + the small pure
helpers (category normalization, the CSV loader, the Airtable row the contact is
pushed into). All I/O — Apify scrape, enrichment, Apollo, Reply.io create, Airtable
upsert — lives in the runner, so this stays trivially testable.
"""

from __future__ import annotations

import csv
import io

# The heat kind + weight (registered in scoring.POINTS). One touch per contact, so a
# serial liker can't inflate an account — same rule as every other engagement kind.
HEAT_KIND = "linkedin_tofu"

# Hardcoded onto every lead/contact from this flow (the Zapier "UTM Campaign" value).
UTM_CAMPAIGN = "TOFU Engagement Campaign"

# Reply.io campaign per category (ids pulled live from the Reply.io campaigns API,
# 2026-06-22). Contacts from a post in <category> are pushed into <campaign>.
CATEGORY_TO_CAMPAIGN: dict[str, int] = {
    "ortho": 1709709,           # Engagement Ortho
    "health systems": 1709710,  # Engagement Health Systems
    "payers": 1709711,          # Engagement Payers
    "behavioural": 1709712,     # Engagement Behavioural
    "radiology": 1709713,       # Engagement Radiology
    "anesthesia": 1709714,      # Engagement Anesthesiology
}

# Category -> our scoring segment (so the engaged account files under the right rubric).
CATEGORY_TO_SEGMENT: dict[str, str] = {
    "ortho": "specialty",
    "radiology": "specialty",
    "anesthesia": "specialty",
    "behavioural": "specialty",
    "health systems": "health_system",
    "payers": "payer",
}

# The two maps must cover the same categories — a category with a campaign but no
# segment (or vice versa) would silently mis-file accounts. Fail loudly at import.
assert set(CATEGORY_TO_CAMPAIGN) == set(CATEGORY_TO_SEGMENT), "category maps drifted"

# Spelling/format drift in the CSV or LinkedIn → our canonical category key.
_ALIASES: dict[str, str] = {
    "behavioral": "behavioural",
    "anesthesiology": "anesthesia",
    "anaesthesia": "anesthesia",
    "health system": "health systems",
    "healthsystems": "health systems",
    "payer": "payers",
    "orthopedics": "ortho",
    "orthopaedics": "ortho",
    "radiology/imaging": "radiology",
    "imaging": "radiology",
}


def normalize_category(value: str | None) -> str | None:
    """Map a raw category label (CSV or LinkedIn) to a canonical key, or None."""
    key = (value or "").strip().lower()
    key = _ALIASES.get(key, key)
    return key if key in CATEGORY_TO_CAMPAIGN else None


def campaign_for(category: str | None) -> int | None:
    """Reply.io campaign id for a category (canonicalized), or None."""
    c = normalize_category(category)
    return CATEGORY_TO_CAMPAIGN.get(c) if c else None


def segment_for(category: str | None) -> str | None:
    """Scoring segment for a category (canonicalized), or None."""
    c = normalize_category(category)
    return CATEGORY_TO_SEGMENT.get(c) if c else None


def post_url(share_id: str) -> str:
    """The LinkedIn feed URL the reactions actor takes, built from a share id."""
    return f"https://www.linkedin.com/feed/update/urn:li:share:{share_id}"


def load_share_categories(csv_text: str) -> dict[str, str]:
    """Parse the `share_id,category` CSV into {share_id: canonical_category}.

    Tolerant of header casing/spacing and quoted ids; skips rows whose category
    isn't one we recognize (logged by the caller, not dropped silently here)."""
    out: dict[str, str] = {}
    reader = csv.DictReader(io.StringIO(csv_text))
    field_map = {(k or "").strip().lower(): k for k in (reader.fieldnames or [])}
    sid_key = field_map.get("share_id") or field_map.get("shareid") or field_map.get("id")
    cat_key = field_map.get("category") or field_map.get("segment")
    if not (sid_key and cat_key):
        raise ValueError("CSV needs 'share_id' and 'category' columns")
    for row in reader:
        sid = str(row.get(sid_key) or "").strip()
        cat = normalize_category(row.get(cat_key))
        if sid and cat:
            out[sid] = cat
    return out


# Airtable column headers in the "LinkedIn <> Airtable" table (base AIRTABLE_BASE_ID).
# UTM_* are constant for this flow, mirroring the existing TOFU rows in that table.
_UTM_SOURCE = "linkedin"
_UTM_MEDIUM = "paid-social"


def build_airtable_fields(*, company: str, email: str | None = None,
                          first_name: str | None = None,
                          last_name: str | None = None, title: str | None = None,
                          phone: str | None = None, linkedin_url: str | None = None) -> dict:
    """Build the Airtable row for the "LinkedIn <> Airtable" table.

    The upsert merge key is Email when present, else LinkedIn URL — a phone-only
    lead (2026-07-08 rule: email OR phone qualifies) still gets a stable key.
    Only non-empty optionals are written (we never overwrite a cell with a
    blank). UTM_* match the existing TOFU rows. (Note: an unused "ABM Match"
    select field exists in the table from the withdrawn capture-all proposal —
    intentionally NOT written until Galyna decides; see the Linear ticket.)
    """
    if not ((email and email.strip()) or (linkedin_url and str(linkedin_url).strip())):
        raise ValueError("Airtable row requires Email or LinkedIn URL (the upsert key)")
    if not (company and company.strip()):
        raise ValueError("Airtable row requires a non-empty Company Name")
    fields: dict[str, str] = {
        "Company Name": company.strip(),
        "UTM Source": _UTM_SOURCE,
        "UTM Medium": _UTM_MEDIUM,
        "UTM Campaign": UTM_CAMPAIGN,
    }
    for key, val in (("Email", email), ("First Name", first_name), ("Last Name", last_name),
                     ("Title", title), ("Phone", phone), ("LinkedIn URL", linkedin_url)):
        if val and str(val).strip():
            fields[key] = str(val).strip()
    return fields
