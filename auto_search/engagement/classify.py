"""Claude classifier for engaged accounts that have no scored framework.

Buckets a company into Magical's ICP framework (health_system / payer / specialty) or
non_icp, from its name + domain — a cheap, self-contained call (no web search). A HARD
confidence gate is the caller's job: trust ONLY 'high' confidence and leave everything
else Unclassified, so we never route a lead to the wrong AE on a guess (per the user:
"no wrong can be tolerated").
"""

from __future__ import annotations

import logging

from auto_search import llm

logger = logging.getLogger(__name__)

# The routing buckets (health_system/payer/specialty) match SPECIALTY_AE keys; non_icp is
# "not a provider/payer" (device makers, vendors, associations…) — never routed.
FRAMEWORKS = ("health_system", "payer", "specialty", "non_icp")
CONFIDENCE = ("high", "medium", "low")

_SYSTEM = """You classify US healthcare companies into Magical's ICP buckets. Magical sells \
back-office / revenue-cycle AI automation to healthcare PROVIDERS and PAYERS.

Buckets:
- health_system: hospitals, health systems, IDNs, academic medical centers, hospital \
networks, critical-access & rural hospitals, FQHCs, independent hospitals.
- payer: health plans, insurers, managed-care organizations, TPAs, PBMs.
- specialty: specialty provider groups — orthopedics, physical therapy / rehab, \
behavioral & mental health, radiology / imaging, telehealth / virtual care, dental, \
physician groups, ambulatory / surgery centers, home health, addiction treatment.
- non_icp: NOT a provider or payer — medical DEVICE or pharma manufacturers, health-IT / \
software vendors, staffing / consulting firms, associations / societies, pure government \
agencies, or anything not clearly a healthcare provider or payer.

Reply with ONLY a JSON object: {"framework":"<bucket>","confidence":"high|medium|low",\
"reason":"<=12 words"}. Use "high" ONLY when the type is unambiguous from the name/domain. \
When unsure, use "low"."""


async def classify_account(name: str, domain: str | None = None) -> dict:
    """Return {framework, confidence, reason}. framework in FRAMEWORKS, confidence in
    CONFIDENCE. Any error / unrecognized output degrades to confidence='low' with
    reason='classify error', so callers can tell a real low-confidence read from an
    outage. Callers gate on confidence: engagement routing trusts only 'high'; the
    CSV import keeps non-high rows but flags them for human/scorer verification."""
    who = (name or "").strip()
    if not who:
        return {"framework": "non_icp", "confidence": "low", "reason": "no name"}
    user = f"Company: {who}" + (f"\nDomain: {domain}" if domain else "")
    try:
        resp = await llm.call_plain(system=_SYSTEM, user_message=user,
                                    max_tokens=120, temperature=0)
        data = llm.parse_json_object(llm.extract_text(resp))
    except Exception:  # noqa: BLE001 — a classify failure must never break the caller
        logger.exception("classify failed for %s", who)
        return {"framework": "non_icp", "confidence": "low", "reason": "classify error"}
    fw = str(data.get("framework") or "").strip().lower()
    conf = str(data.get("confidence") or "low").strip().lower()
    if fw not in FRAMEWORKS:
        return {"framework": "non_icp", "confidence": "low", "reason": "unrecognized bucket"}
    return {
        "framework": fw,
        "confidence": conf if conf in CONFIDENCE else "low",
        "reason": str(data.get("reason") or "")[:80],
    }
