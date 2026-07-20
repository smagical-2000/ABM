"""Phone-resolution waterfall — cost-ordered, first-hit-wins (2026-07-09).

Enrichment providers resolve work emails reliably but return phones for only a
portion of contacts. So a lead's phone is resolved by cascading through sources
in COST order and stopping at the first valid number:

  1. Apollo        — already fetched by the caller during the email match (free,
                     in-pipeline). Passed in as `apollo_phone`.
  2. Salesforce    — a number sales entered manually on a matching Lead/Contact
                     (free). Supplied by the caller as an async `sfdc_lookup`
                     (email -> phone|None); omitted where SFDC isn't reachable.
  3. FullEnrich    — paid provider, one credit per lookup. Only reached when the
                     free tiers miss, and only when `allow_fullenrich` is True
                     (the caller uses that to enforce a per-run credit cap).

  (Clay is the planned next tier and slots in above FullEnrich when built.)

This replaces the old rule that skipped the paid lookup for non-ABM leads that
already had an email — which left real leads (e.g. non-target healthcare
reactors) without a phone. Now every lead cascades the free tiers first and only
non-ABM volume, not ABM status, is what the per-run cap bounds.

Pure orchestration: the only I/O is the FullEnrich call and the caller's
sfdc_lookup, both awaited. No provider client is imported here, so the tiers are
trivially testable with fakes.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from auto_search.engagement import enrichment

logger = logging.getLogger(__name__)

SfdcLookup = Callable[[str], Awaitable[str | None]]


def _clean(v: str | None) -> str | None:
    v = (v or "").strip()
    return v or None


async def resolve_phone(
    *,
    first_name: str | None,
    last_name: str | None,
    email: str | None = None,
    domain: str | None = None,
    company: str | None = None,
    linkedin: str | None = None,
    apollo_phone: str | None = None,
    sfdc_lookup: SfdcLookup | None = None,
    allow_fullenrich: bool = True,
    http=None,
) -> tuple[str | None, str | None, bool]:
    """Resolve one lead's phone via the waterfall. Returns
    (phone, source, fullenrich_attempted) where source is 'apollo' | 'salesforce'
    | 'fullenrich' (None if no tier produced a number). `fullenrich_attempted` is
    True whenever the PAID tier was actually called — hit or miss — so the caller
    can cap paid ATTEMPTS, not just successes (a run of misses still spends
    credits). Never raises: a tier that errors is logged and skipped so a lead is
    never lost to an enrichment hiccup."""
    # Tier 1 — Apollo (already in hand)
    if _clean(apollo_phone):
        return apollo_phone.strip(), "apollo", False

    # Tier 2 — Salesforce (sales-entered, free). Best-effort.
    if sfdc_lookup and _clean(email):
        try:
            ph = await sfdc_lookup(email.strip())
            if _clean(ph):
                return ph.strip(), "salesforce", False
        except Exception:  # noqa: BLE001 — a SFDC blip must never drop the lead
            logger.warning("phone waterfall: SFDC lookup failed for %s", email)

    # Tier 3 — FullEnrich (paid). Needs a name and at least one identity key.
    if allow_fullenrich and (_clean(first_name) or _clean(last_name)) \
            and (_clean(email) or _clean(linkedin)):
        attempted = True    # the call below is billed whether or not it finds a phone
        try:
            fe = await enrichment.enrich_contact(
                first_name=first_name, last_name=last_name, domain=domain,
                company=company, linkedin=linkedin, http=http)
            if _clean(fe.get("phone")):
                return fe["phone"].strip(), "fullenrich", True
        except Exception:  # noqa: BLE001 — enrichment must never break the lead flow
            logger.exception("phone waterfall: FullEnrich failed for %s %s",
                             first_name, last_name)
    else:
        attempted = False

    return None, None, attempted
