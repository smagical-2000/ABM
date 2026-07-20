"""CMS Medicare Shared Savings Program (MSSP) ACO participation lookup.

Public CMS dataset (~511 rows, one per ACO): name, track (Basic/Enhanced),
service area, agreement dates, and leadership names. If an account runs or
fronts an ACO, that is a strong value-based-care sophistication signal (and
the exec/medical-director names are useful to a seller), so matched facts are
injected into the AE-lookup KNOWN FACTS and flow into the score + brief.

The dataset is tiny and changes ~annually: fetch it ALL once, cache in-process
for a day, and match locally by normalized-name containment + significant-token
overlap — no dependency on the API's filter syntax. Best-effort everywhere:
any failure returns no facts, never an error. Names/titles only — the dataset's
emails/phones are deliberately not surfaced.
"""

from __future__ import annotations

import logging
import time

import httpx

from auto_search.normalize import normalize_company_name

logger = logging.getLogger(__name__)

_DATASET = "69ec2609-5ce5-4ce1-b14c-1f8809fda2c2"
_URL = f"https://data.cms.gov/data-api/v1/dataset/{_DATASET}/data"
_TIMEOUT_S = 30
_PAGE = 1000
_CACHE_TTL_S = 24 * 3600
_MAX_MATCHES = 3

# Generic tokens that must never carry a match on their own ("Health Partners"
# would otherwise match half the file).
_STOP = frozenset({
    "aco", "accountable", "care", "health", "healthcare", "medical", "medicine",
    "network", "alliance", "partners", "partner", "group", "system", "systems",
    "services", "service", "of", "the", "and", "for", "at", "inc", "llc",
    "community", "regional", "center", "centers", "associates", "physicians",
    "clinic", "clinics", "senior", "quality", "value", "collaborative",
})

_cache: dict = {"at": 0.0, "rows": []}


def _tokens(name: str) -> set[str]:
    import re
    words = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).split()
    return {w for w in words if len(w) > 2 and w not in _STOP}


def fetch_acos(*, force: bool = False) -> list[dict]:
    """All MSSP ACO rows, cached in-process for a day. [] on any failure."""
    now = time.time()
    if not force and _cache["rows"] and now - _cache["at"] < _CACHE_TTL_S:
        return _cache["rows"]
    rows: list[dict] = []
    try:
        offset = 0
        while True:
            r = httpx.get(_URL, params={"size": _PAGE, "offset": offset},
                          timeout=_TIMEOUT_S)
            r.raise_for_status()
            page = r.json()
            if not isinstance(page, list) or not page:
                break
            rows.extend(x for x in page if isinstance(x, dict))
            if len(page) < _PAGE:
                break
            offset += _PAGE
    except Exception as e:  # noqa: BLE001 — enrichment is best-effort
        logger.warning("CMS ACO fetch failed: %s", e)
        return _cache["rows"]          # stale cache beats nothing
    _cache.update(at=now, rows=rows)
    logger.info("CMS ACO dataset loaded: %d ACOs", len(rows))
    return rows


def match_acos(company_name: str, acos: list[dict] | None = None) -> list[dict]:
    """ACOs plausibly run/fronted by this organization. Precision-first (these
    land in KNOWN FACTS): a match needs normalized-name containment, >=2 shared
    SIGNIFICANT tokens, or one side's significant tokens fully inside the
    other's when the branded token is distinctive (>=6 chars — "ochsner",
    "ascension"). Generic healthcare words never carry a match."""
    key = normalize_company_name(company_name)
    toks = _tokens(company_name)
    if not key:
        return []
    out = []
    for a in (acos if acos is not None else fetch_acos()):
        aco_name = a.get("aco_name") or ""
        aco_key = normalize_company_name(aco_name)
        aco_toks = _tokens(aco_name)
        if not aco_key:
            continue
        contained = key in aco_key or aco_key in key
        shared = toks & aco_toks
        branded_subset = bool(toks and aco_toks
                              and (toks <= aco_toks or aco_toks <= toks)
                              and any(len(t) >= 6 for t in shared))
        if contained or len(shared) >= 2 or branded_subset:
            out.append(a)
    return out[:_MAX_MATCHES]


def _track(a: dict) -> str:
    if a.get("enhanced_track") == "1":
        return "Enhanced track"
    if a.get("basic_track") == "1":
        lvl = a.get("basic_track_level")
        return f"Basic track {lvl}" if lvl and lvl != "N/A" else "Basic track"
    return "track unknown"


def aco_known_facts(company_name: str) -> dict[str, str]:
    """KNOWN FACTS block for the scorer/brief: one line per matched ACO.
    Names/titles only, never the dataset's emails or phones."""
    matches = match_acos(company_name)
    if not matches:
        return {}
    lines = []
    for a in matches:
        bits = [f"{a.get('aco_name')} ({a.get('aco_id')})", _track(a)]
        if a.get("aco_service_area"):
            bits.append(f"service area {a['aco_service_area']}")
        if a.get("initial_start_date"):
            bits.append(f"in MSSP since {a['initial_start_date'][-4:]}")
        people = []
        if a.get("aco_exec_name"):
            people.append(f"exec {a['aco_exec_name']}")
        if a.get("aco_medical_director_name"):
            people.append(f"medical director {a['aco_medical_director_name']}")
        if people:
            bits.append(", ".join(people))
        lines.append(" · ".join(bits))
    label = ("ACO participation (CMS Medicare Shared Savings Program)"
             if len(lines) == 1 else
             "ACO participation (CMS MSSP; closest name matches)")
    return {label: " | ".join(lines)}
