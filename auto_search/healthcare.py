"""Healthcare ICP classification — one gate, shared by every connector.

Magical sells to healthcare PROVIDERS and PAYERS (hospitals, specialty
practices, behavioral health, health systems, MCOs). Pharma, biotech, medical
devices, and research labs are explicit ICP disqualifiers — even though
LinkedIn often labels them "Hospitals and Health Care" too.

Two SignalBase fields inform the call:
  • `companyIndustry`    — LinkedIn industry label (free text, 434 values)
  • `companySubcategory` — SignalBase's strict enum (e.g. "healthcare",
                           "biotechnology"); the reliable discriminator

Example this guards against: "Curevo Vaccine" has industry "Hospitals and
Health Care" but subcategory "biotechnology" — a vaccine developer, not a
provider. The subcategory check excludes it.

Centralised here so the leadership and acquisition connectors classify
healthcare identically — no drift, no duplicated keyword lists.
"""

from __future__ import annotations

# Provider / payer LinkedIn industry labels we accept. Substring match on the
# lowercased industry, so "Hospitals and Health Care", "Medical Practices",
# "Mental Health Care", "Home Health Care Services", "Nursing Homes and
# Residential Care Facilities", etc. all pass.
_PROVIDER_INDUSTRY_INCLUDE = (
    "health care", "hospitals", "medical practice", "mental health",
    "behavioral health", "nursing", "home health", "ambulatory",
    "outpatient care", "health system", "physicians",
    # payers
    "insurance carriers", "health insurance", "insurance and employee benefit",
)

# Industries that look healthcare-ish but are out of ICP. Checked first so a
# biotech labelled "Hospitals and Health Care" is still excluded. "hospitality"
# is here because it contains the substring "hospital".
_INDUSTRY_EXCLUDE = (
    "pharmaceutical", "biotechnology", "medical device",
    "medical equipment", "research services", "hospitality",
    "veterinary",
)

# SignalBase strict subcategory enum values that disqualify regardless of the
# (often noisy) industry label.
_SUBCATEGORY_EXCLUDE = frozenset({"biotechnology", "science"})

# Subcategory that affirmatively confirms a healthcare provider/payer.
_SUBCATEGORY_INCLUDE = frozenset({"healthcare", "insurance"})


def is_healthcare_provider(
    industry: str | None,
    subcategory: str | None = None,
) -> bool:
    """True if the company is a healthcare provider/payer in Magical's ICP.

    Decision order (most authoritative first):
      1. Excluded subcategory (biotech/science) → False, no matter the label.
      2. Included subcategory (healthcare/insurance) → True.
      3. Excluded industry substring (pharma/device/hospitality/…) → False.
      4. Included provider/payer industry substring → True.
      5. Otherwise → False.
    """
    sub = (subcategory or "").strip().lower()
    if sub in _SUBCATEGORY_EXCLUDE:
        return False
    if sub in _SUBCATEGORY_INCLUDE:
        return True

    ind = (industry or "").lower()
    if not ind:
        return False
    if any(x in ind for x in _INDUSTRY_EXCLUDE):
        return False
    return any(x in ind for x in _PROVIDER_INDUSTRY_INCLUDE)


# Pipe-separated LinkedIn industry labels for SignalBase's `categories` filter
# (server-side narrowing). Providers/payers only — pharma/biotech industries
# are intentionally omitted so they're filtered out server-side. The client
# still applies is_healthcare_provider() as the authority (biotech can hide
# under a provider label, as the Curevo case shows).
CATEGORIES_FILTER = "|".join((
    "Hospitals and Health Care",
    "Hospitals",
    "Medical Practices",
    "Mental Health Care",
    "Nursing Homes and Residential Care Facilities",
    "Home Health Care Services",
    "Outpatient Care Centers",
))
