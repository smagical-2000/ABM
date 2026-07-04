"""AE one-off lookup — resolve a typed company BEFORE any deep-score spend.

An AE gives a company name (+ optionally a website). Resolution answers, in
order, the three questions that protect accuracy and money:

  1. Do we already have it?   -> scored account (jump there) or a live
     Discovery company (promote WITH its signals — never make a signal-less twin).
  2. Who exactly is it?       -> Exa web retrieval + a Claude identity pass,
     confidence-gated IN CODE: low confidence, a domain that contradicts the
     AE's input, or non-ICP never silently proceeds — the AE confirms.
  3. Only then does the caller enqueue the account into the NORMAL scoring
     pipeline (same engine, same rubric, same independent QA), so a one-off
     score can never diverge from a batch score.

Pure module: repos/clients arrive as injected callables, so every outcome is
unit-testable without network or DB.
"""

from __future__ import annotations

import asyncio
import logging
import textwrap
from typing import Any

from auto_search import llm
from auto_search.clients import exa
from auto_search.normalize import normalize_company_name
from auto_search.scoring.frameworks import framework_for_segment
from auto_search.scoring.models import Account

logger = logging.getLogger(__name__)

_MAX_TOKENS = 700
_RESULTS = 5
SEGMENTS = ("health_system", "payer", "specialty")   # scoreable rubrics
# resolve() statuses (the UI contract): already_scored | in_discovery | new |
# ambiguous | non_icp | unresolved


# ── question 1: what do we already have? ──────────────────────────────


def find_existing(name: str, domain: str | None, *,
                  accounts: list[dict], get_company) -> tuple[str | None, Any]:
    """('scored', row) | ('discovery', company) | (None, None).

    Identity is the same one the CSV importer trusts: the normalized-name key,
    plus a domain equality check that also catches renamed accounts.
    """
    key = normalize_company_name(name)
    dom = (domain or "").lower()
    for row in accounts:
        row_dom = (row.get("domain") or "").lower()
        if (dom and row_dom and dom == row_dom) or \
                (key and normalize_company_name(row.get("name") or "") == key):
            return "scored", row
    company = get_company(key) if key else None
    if company is not None and getattr(company, "icp_status", None) in (
            "qualified", "needs_review"):
        return "discovery", company
    return None, None


# ── question 2: who exactly is it? (Exa + gated identity pass) ────────


_IDENTITY_SYSTEM = textwrap.dedent("""
    You resolve which company a healthcare salesperson means, from web search
    results. You work for Magical, which sells agentic-AI revenue cycle
    management to US healthcare organizations.

    Rules:
    - Use ONLY the provided results. Never invent facts, domains, or numbers.
    - The salesperson's input can be misspelled or partial; the results decide.
    - segment buckets: "health_system" (hospitals, health systems, IDNs),
      "payer" (insurers, health plans, payer services), "specialty" (specialty
      provider groups: PT/rehab, behavioral, ortho, dental, home health, etc.),
      or "non_icp" (not a US healthcare provider/payer at all).
    - confidence: "high" only when the results clearly identify ONE company
      consistent with the input; "medium" when likely but thinly evidenced;
      "low" when conflicting, ambiguous, or unsupported.
    - If distinct similarly-named companies appear, list them in "alternates".

    Return ONLY this JSON object, no prose:
    {
      "matched": true|false,
      "name": "<canonical company name>",
      "domain": "<bare primary domain, e.g. ivyrehab.com>",
      "segment": "health_system|payer|specialty|non_icp",
      "sub_segment": "<short niche label or null>",
      "confidence": "high|medium|low",
      "description": "<1-2 factual sentences: what they are, size/footprint if stated>",
      "hq": "<city, state or null>",
      "approximate_employees": <integer or null>,
      "evidence_url": "<the single most identifying result URL>",
      "reason": "<1 sentence why this is the right company>",
      "alternates": [{"name": "...", "domain": "...", "description": "..."}]
    }
""").strip()


def _results_block(results: list[exa.ExaResult]) -> str:
    lines = []
    for i, r in enumerate(results, 1):
        text = " ".join((r.text or "").split())[:600]
        lines.append(f"[{i}] {r.title}\n    url: {r.url}\n    text: {text}")
    return "\n".join(lines)


async def _identify(name: str, domain: str | None,
                    results: list[exa.ExaResult]) -> tuple[dict, float]:
    """One no-tools Claude pass over the Exa results. Returns (parsed, cost)."""
    user = (
        f"Salesperson input — company name: {name!r}"
        + (f", website: {domain!r}" if domain else ", website: not given")
        + "\n\nWeb results:\n" + _results_block(results)
        + "\n\nRespond with ONLY the JSON object."
    )
    response = await llm.call_plain(system=_IDENTITY_SYSTEM, user_message=user,
                                    max_tokens=_MAX_TOKENS, temperature=0)
    cost = llm.call_cost(response)
    parsed = llm.parse_json_object(llm.extract_text(response))
    return parsed, cost


def _gate(name: str, input_domain: str | None, ident: dict) -> dict:
    """Turn the model's identity JSON into a resolve outcome — IN CODE.

    The model proposes; this decides. Anything not clearly one ICP company
    becomes 'ambiguous'/'non_icp'/'unresolved' so the AE explicitly chooses —
    a wrong-company deep score is the one failure this feature cannot have.
    """
    resolved = {
        "name": str(ident.get("name") or name).strip(),
        "domain": exa.domain_of(str(ident.get("domain") or "")) or None,
        "segment": str(ident.get("segment") or "").strip(),
        "sub_segment": (str(ident.get("sub_segment")).strip()
                        if ident.get("sub_segment") else None),
        "confidence": str(ident.get("confidence") or "low").strip().lower(),
        "description": str(ident.get("description") or "").strip(),
        "hq": (str(ident.get("hq")).strip() if ident.get("hq") else None),
        "approximate_employees": ident.get("approximate_employees"),
        "evidence_url": str(ident.get("evidence_url") or "").strip() or None,
        "reason": str(ident.get("reason") or "").strip(),
    }
    alternates = [
        # Normalize like resolved.domain so the AE never sees www./URL noise on
        # one option and a bare domain on another (build_account re-validates).
        {"name": str(a.get("name") or ""),
         "domain": exa.domain_of(str(a.get("domain") or "")),
         "description": str(a.get("description") or "")}
        for a in (ident.get("alternates") or []) if isinstance(a, dict)
    ]
    ok_conf = resolved["confidence"] in ("high", "medium")
    if not ident.get("matched") or not ok_conf or not resolved["domain"]:
        return {"status": "unresolved", "resolved": resolved, "alternates": alternates}
    if input_domain and resolved["domain"] != input_domain:
        # The model disagrees with the website the AE typed — never pick silently.
        return {"status": "ambiguous", "resolved": resolved, "alternates": alternates}
    if resolved["segment"] == "non_icp":
        return {"status": "non_icp", "resolved": resolved, "alternates": alternates}
    if resolved["segment"] not in SEGMENTS:
        return {"status": "unresolved", "resolved": resolved, "alternates": alternates}
    return {"status": "new", "resolved": resolved, "alternates": alternates}


async def resolve(name: str, website: str | None, *,
                  accounts: list[dict], get_company,
                  search=None) -> dict:
    """Full resolve: existing check first (free), then Exa + identity (paid).

    Returns {"status", ...} per outcome; paid outcomes carry "cost_usd" for the
    caller's cost event. Exa/LLM failures degrade to 'unresolved' (manual entry),
    never to a guess.

    `search` defaults to exa.search at CALL time (late-bound), so tests that
    patch the exa module are honored even through the default path.
    """
    search = search or exa.search
    name = (name or "").strip()
    if not name:
        raise ValueError("company name is required")
    domain = exa.domain_of(website) or None

    kind, hit = find_existing(name, domain, accounts=accounts, get_company=get_company)
    if kind == "scored":
        return {"status": "already_scored", "account_id": hit.get("account_id"),
                "account": {k: hit.get(k) for k in (
                    "account_id", "name", "domain", "state", "tier", "tier_label",
                    "total", "max_total", "segment", "scored_at")}}
    if kind == "discovery":
        signals = list(getattr(hit, "signals", None) or [])
        return {"status": "in_discovery",
                "company": {"key": getattr(hit, "company_key", None),
                            "name": getattr(hit, "name", name),
                            "segment": getattr(hit, "segment", None),
                            "signals": len(signals)}}

    query = f"{name} {domain} company" if domain else f"{name} company US healthcare"
    try:
        # exa.search is sync httpx (25s timeout) — run it off the event loop so
        # a slow search can't stall every other request on the async API.
        results = await asyncio.to_thread(search, query, num_results=_RESULTS)
    except exa.ExaError as e:
        logger.warning("lookup: exa failed for %r: %s", name, e)
        return {"status": "unresolved", "error": str(e), "cost_usd": 0.0}
    if not results:
        return {"status": "unresolved", "error": "no web results",
                "cost_usd": exa.search_cost(0)}

    cost = exa.search_cost(len(results))
    try:
        ident, llm_cost = await _identify(name, domain, results)
        cost = round(cost + llm_cost, 4)
    except Exception as e:  # noqa: BLE001 — degrade to manual entry, never guess
        logger.warning("lookup: identity pass failed for %r: %s", name, e)
        return {"status": "unresolved", "error": f"identity pass failed: {e}",
                "cost_usd": cost}

    out = _gate(name, domain, ident)
    out["cost_usd"] = cost
    return out


# ── question 3: the scoreable account (commit step) ───────────────────


def build_account(*, name: str, domain: str, segment: str,
                  sub_segment: str | None = None, description: str = "",
                  hq: str | None = None, evidence_url: str | None = None,
                  approximate_employees: int | None = None) -> Account:
    """The Account an AE-confirmed lookup enqueues. Identity facts ONLY go into
    KNOWN FACTS (website/profile/HQ) — never rubric numbers (NPR, beds, lives),
    which the scorer must research itself with citations, exactly like batch."""
    if segment not in SEGMENTS:
        raise ValueError(f"segment must be one of {SEGMENTS}")
    key = normalize_company_name(name)
    if not key:
        raise ValueError("company name is required")
    dom = exa.domain_of(domain)
    if not dom:
        raise ValueError("a valid company domain is required")
    facts: dict[str, Any] = {"Company website": dom}
    if description:
        facts["Company profile"] = description
    if hq:
        facts["Headquarters"] = hq
    if evidence_url:
        facts["Identity evidence URL"] = evidence_url
    return Account(
        account_id="acc_" + key,
        name=name,
        segment=segment,
        framework=framework_for_segment(segment).key,
        source="ae",
        domain=dom,
        sub_segment=sub_segment,
        approximate_employees=approximate_employees,
        firmographics=facts,
    )
