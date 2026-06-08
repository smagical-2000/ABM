"""Company-name normalization — the single source of truth for dedup.

Why this module exists
----------------------
Deduplication is the whole point of the discovery pipeline: we must never
pay Claude twice to qualify the same company, and we must never insert the
same company twice into the database. That only works if EVERY part of the
system normalizes a company name the exact same way.

Before this module, normalization was duplicated in three places (the
connector's external-id builder, the trace-file slug, and the planned DB
key). Three implementations means three chances for "Advanced Specialty
Hospitals of Toledo" to normalize differently — which silently breaks dedup.

This module is that one implementation. Import from here; never re-roll it.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

# Legal-entity suffixes and filler words that don't help identify a company.
# Stripped before generating the dedup key so "Acme Health LLC" and
# "Acme Health, Inc." collapse to the same key.
_ENTITY_SUFFIXES = (
    "inc", "incorporated", "llc", "llp", "lp", "ltd", "limited",
    "corp", "corporation", "co", "company", "plc", "pllc", "pc",
    "group", "holdings", "holding", "partners", "associates",
)

# Characters that are noise for matching (collapse to a single space first).
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_MULTISPACE = re.compile(r"\s+")


def normalize_company_name(name: str) -> str:
    """Return the canonical dedup key for a company name.

    The key is lowercase, punctuation-free, suffix-stripped, and space-free.
    Two names that refer to the same company should produce the same key.

    Examples:
        "Advanced Specialty Hospitals of Toledo"  -> "advancedspecialtyhospitalsoftoledo"
        "Acme Health, LLC"                        -> "acmehealth"
        "Acme Health Inc."                        -> "acmehealth"
        "OrthoIndy"                               -> "orthoindy"

    Note: this is intentionally aggressive (removes ALL spaces) to maximise
    collision for true duplicates. Fuzzy matching for near-duplicates is a
    separate concern handled at the DB layer if/when we need it.
    """
    if not name:
        return ""

    # Lowercase + replace any run of non-alphanumerics with a single space.
    cleaned = _NON_ALNUM.sub(" ", name.lower()).strip()

    # Drop trailing legal-entity suffix words ("acme health llc" -> "acme health").
    words = [w for w in cleaned.split(" ") if w]
    while words and words[-1] in _ENTITY_SUFFIXES:
        words.pop()

    # Join with no separator — the key is for equality, not readability.
    return "".join(words)


def slugify(name: str, *, max_len: int = 50) -> str:
    """Return a filesystem/URL-safe slug (keeps word boundaries as underscores).

    Used for trace filenames and human-readable IDs — NOT for dedup.
    For dedup, always use normalize_company_name().

    Example:
        "Advanced Specialty Hospitals of Toledo" -> "advanced_specialty_hospitals_of_toledo"
    """
    slug = _NON_ALNUM.sub("_", name.lower()).strip("_")
    return slug[:max_len]


def parse_int_loose(value: object) -> int | None:
    """Best-effort int parse for messy LLM / scraped values.

    Handles "2,400", "~2400", "2400 employees", "approx. 500", 2400, 2400.0.
    Returns None when no integer can be recovered.

    Centralised here so the connector and the qualifier coerce numbers the
    same way (another silent-divergence risk if duplicated).
    """
    if value is None:
        return None
    if isinstance(value, bool):          # bool is an int subclass — reject it
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)

    # String path: pull the first run of digits (after stripping thousands separators).
    digits = re.search(r"\d[\d,]*", str(value))
    if not digits:
        return None
    try:
        return int(digits.group(0).replace(",", ""))
    except ValueError:
        return None


def parse_iso_datetime(s: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp (or plain date) to an aware UTC datetime.

    Handles the SignalBase shape ('2026-05-30T01:19:36.672Z') plus bare dates
    ('2026-05-30', '2026-05'). Returns None when nothing parses. Shared by
    every connector so date handling can't drift between sources.
    """
    s = (s or "").strip()
    if not s:
        return None
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%Y-%m-%d", "%Y-%m"):
            try:
                parsed = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    # fromisoformat on a date-only string ('2026-04-10') yields a NAIVE
    # datetime; force UTC so callers can always compare against aware cutoffs.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def clean_domain(website: str | None) -> str | None:
    """Return a bare domain, or None if the value isn't a clean domain.

    SignalBase sometimes returns vanity/short links (e.g. 'co.jll/4ozae4w');
    we'd rather store nothing than a broken domain — the qualifier finds the
    real site anyway. Accepts 'orthoindy.com', rejects anything with a path,
    a space, or no dot.
    """
    w = (website or "").strip().lower()
    if not w or "/" in w or " " in w or "." not in w:
        return None
    return w


def normalize_linkedin_url(url: str | None) -> str:
    """Canonical key for a LinkedIn profile/company URL, for dedup.

    Lowercases, drops scheme / 'www.' / regional subdomain (ca., uk.), query
    string, and trailing slash, so 'https://ca.linkedin.com/company/getmagical/'
    and 'http://www.linkedin.com/company/getmagical' collapse to the same key.
    """
    u = (url or "").strip().lower()
    u = re.sub(r"^[a-z]+://", "", u)
    u = re.sub(r"^[a-z]{2}\.", "", u)        # regional subdomain (ca./uk./…)
    u = re.sub(r"^www\.", "", u)
    u = u.split("?")[0].split("#")[0]
    return u.rstrip("/")


def normalize_keyword(keyword: str | None) -> str:
    """Dedup key for an event/search keyword — case/quote/space-insensitive, so
    '"HIMSS26"', 'himss26', and ' HIMSS26 ' collapse to one."""
    return (keyword or "").strip().strip('"').strip("'").lower()
