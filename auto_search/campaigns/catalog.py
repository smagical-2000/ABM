"""Sequence catalog — pure ICP -> sequence-key mapping (no I/O).

Which outbound sequence an account belongs to, keyed by the account's scoring
segment + sub-vertical. The actual Reply.io campaign id per key is DATA, not
code: Galyna assigns it in the Campaigns tab (stored via campaign_repository
`campaign_sequences`), because sequences are authored in the Reply.io UI and
their ids only exist once she builds them. Mirrors how linkedin_ads.py maps
category -> campaign, but editable at runtime instead of hardcoded.

The keys track the sequence library in the "Sunny Email Campaign Info" doc
(one content sequence per vertical; podcast/event/persona variants are later
iterations — a new variant is a new key row here, nothing else changes).
"""

from __future__ import annotations

import re

# Ordered: the order the mapping editor lists them (biggest lists first).
SEQUENCE_KEYS: dict[str, dict] = {
    "health_system": {
        "label": "Health Systems",
        "hint": "Article sequence (3 emails) — margin/workforce angle",
    },
    "ortho": {
        "label": "Providers - Ortho",
        "hint": "Article sequence (3 emails) — revenue-leakage angle",
    },
    "behavioral": {
        "label": "Providers - Behavioral Health",
        "hint": "Personalized-video sequence (3 emails)",
    },
    "radiology": {
        "label": "Providers - Radiology/Imaging",
        "hint": "Article sequence (3 emails) — prior-auth angle",
    },
    "anesthesia": {
        "label": "Providers - Anesthesiology",
        "hint": "Article sequence (3 emails) — blog library",
    },
    "payer": {
        "label": "Payers",
        "hint": "Event-invite sequence (3 emails; needs a live event)",
    },
    "specialty_other": {
        "label": "Other Specialties",
        "hint": "Derm / Cardio / Urology / Ophtho / Neuro / Pain — generic sequence",
    },
}

# Sub-vertical detection for specialty accounts. Checked against the account's
# sub_segment + framework (NOT the name first — "Northside Hospital Radiology
# Dept" style names would misroute health systems). Name is the last resort for
# specialty accounts only.
_VERTICAL_RES: tuple[tuple[str, re.Pattern], ...] = (
    ("ortho", re.compile(r"ortho|spine", re.I)),
    ("behavioral", re.compile(r"behavio|mental|psych|substance|sud\b", re.I)),
    ("radiology", re.compile(r"radiolog|imaging", re.I)),
    ("anesthesia", re.compile(r"an[ae]esthes", re.I)),
)


def sequence_key_for(account: dict) -> str:
    """The sequence key for a scored account. Segment decides the branch
    (payer / health_system); specialty accounts route by sub-vertical keywords
    in sub_segment/framework, then name, else the generic specialty key."""
    seg = str(account.get("segment") or "").strip().lower()
    if "payer" in seg:
        return "payer"
    if "health" in seg or "hospital" in seg:
        return "health_system"
    # specialty (or unknown): classify the sub-vertical
    strong = " ".join(str(account.get(k) or "") for k in ("sub_segment", "framework"))
    for key, rx in _VERTICAL_RES:
        if rx.search(strong):
            return key
    name = str(account.get("name") or "")
    for key, rx in _VERTICAL_RES:
        if rx.search(name):
            return key
    return "specialty_other"


def sequence_label(key: str) -> str:
    meta = SEQUENCE_KEYS.get(key)
    return meta["label"] if meta else key
