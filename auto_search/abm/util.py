"""Pure string helpers for ABM matching - domain, US-state, and alias parsing.

Separated from the parser/matcher so each helper is trivially unit-testable and
shared by both. No I/O, no dependencies beyond the stdlib.
"""

from __future__ import annotations

import re

# Postal codes for the 50 states + DC + common territories. Used to validate a
# 2-letter token before we trust it as a location (avoids treating "ER" in
# "Cooper ER" as a state).
US_STATES = frozenset((
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC", "PR", "VI", "GU",
))

# (FKA Foo), (AKA Foo), (DBA Foo), (formerly Foo) - capture the alternate name.
_ALIAS_RE = re.compile(
    r"\(\s*(?:f/?k/?a|a/?k/?a|d/?b/?a|formerly|fka|aka|dba)\b\.?\s*(.*?)\s*\)",
    re.IGNORECASE,
)
_PAREN_RE = re.compile(r"\([^)]*\)")
_WS_RE = re.compile(r"\s+")
# "City, ST" or trailing " ST".
_STATE_TAIL_RE = re.compile(r"[,\s]\s*([A-Za-z]{2})\s*$")


def bare_domain(value: object) -> str | None:
    """Reduce any website cell to a bare registrable domain, or None.

    'https://www.Acme.com/careers' -> 'acme.com'
    'answerhealth.com/'            -> 'answerhealth.com'
    '', 'n/a', 'see website'       -> None
    """
    if not value:
        return None
    s = str(value).strip().lower()
    s = re.sub(r"^[a-z]+://", "", s)     # scheme
    s = re.sub(r"^www\.", "", s)
    s = s.split("/")[0].split("?")[0].strip()
    if not s or " " in s or "." not in s:
        return None
    return s


def split_aliases(raw: str) -> tuple[str, list[str]]:
    """Split a target name into (primary, [aliases]).

    'Overlake Medical Center (FKA Overlake Hospital)'
        -> ('Overlake Medical Center', ['Overlake Hospital'])

    The primary has ALL parentheticals removed (percentages, FKA notes, etc.)
    so it normalizes cleanly; each FKA/AKA alias is returned separately so a
    former name still produces a match key.
    """
    if not raw:
        return "", []
    aliases = [m.strip() for m in _ALIAS_RE.findall(raw) if m and m.strip()]
    primary = _WS_RE.sub(" ", _PAREN_RE.sub(" ", raw)).strip()
    return primary, aliases


def extract_state(value: object) -> str | None:
    """Pull a US 2-letter state code from a cell or location string.

    'NE' -> 'NE';  'Lincoln, NE' -> 'NE';  'Remote' -> None;  'Texas' -> None.
    (We only trust an explicit 2-letter postal code, which both the target
    'State' column and the job-signal location strings use.)
    """
    if not value:
        return None
    s = str(value).strip()
    if len(s) == 2 and s.upper() in US_STATES:
        return s.upper()
    m = _STATE_TAIL_RE.search(s)
    if m and m.group(1).upper() in US_STATES:
        return m.group(1).upper()
    return None


def segment_for_sheet(sheet: str | None) -> str:
    """Human-readable segment label from a workbook sheet name.

    'PGs - Urology' -> 'Physician Group - Urology'; others pass through.
    """
    s = (sheet or "").strip()
    if s.startswith("PGs - "):
        return "Physician Group - " + s[len("PGs - "):].strip()
    return s
