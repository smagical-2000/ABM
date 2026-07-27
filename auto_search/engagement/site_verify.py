"""Same-company verification by looking at the actual sites (2026-07-27).

The merge audit proved name-pattern matching mis-merges real companies
(Healthfirst NY onto Health First FL; Radiology Partners onto Radiology Inc).
The counter-rule the operator asked for: when two records disagree on domain,
LOOK AT THE SITES and decide, never merge on a name pattern alone.

Decision ladder — merge-averse: an auto "same" verdict is only ever issued on
deterministic, high-confidence evidence. Anything softer routes to review.
  1. registrable equality           (no fetch)          -> same/high
  2. redirect convergence           (both land together) -> same/high
  3. cross-canonical                (rel=canonical spans) -> same/high
  4. one redirects into the other's registrable domain  -> same/high
  5. optional adjudication hook (LLM over site metadata) -> same|different / low
  6. else                                                -> unknown

Verdicts cache in the engagement settings JSON (key 'domain_pair_verdicts')
keyed by the sorted registrable pair. Human verdicts are permanent and never
overwritten by auto verdicts; auto verdicts carry decided_by='auto'.

An adjudicated/low 'same' NEVER auto-merges — it only annotates the review
queue. 'different' (any confidence) hard-blocks name-only merges.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from auto_search.normalize import registrable_domain

logger = logging.getLogger(__name__)

_CACHE_KEY = "domain_pair_verdicts"
_FETCH_CAP_BYTES = 400_000
_UA = "abm-scorer-identity-verify/1.0 (+https://usemagical.com)"

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_META_RE = re.compile(
    r"<meta[^>]+(?:property=[\"']og:site_name[\"']|name=[\"']description[\"'])"
    r"[^>]*content=[\"']([^\"']{0,300})", re.I)
_CANONICAL_RE = re.compile(
    r"<link[^>]+rel=[\"']canonical[\"'][^>]*href=[\"']([^\"']+)", re.I)


@dataclass
class SiteIdentity:
    domain: str
    final_host: str = ""
    title: str = ""
    site_name: str = ""
    canonical_host: str = ""
    error: str = ""


@dataclass
class Verdict:
    verdict: str                 # 'same' | 'different' | 'unknown'
    confidence: str              # 'high' | 'low'
    method: str
    evidence: list[str] = field(default_factory=list)


def pair_key(dom_a: str | None, dom_b: str | None) -> str:
    return "|".join(sorted((registrable_domain(dom_a) or "",
                            registrable_domain(dom_b) or "")))


def fetch_site_identity(domain: str, *, timeout: float = 8.0,
                        http=None) -> SiteIdentity:
    """GET the homepage and extract identity metadata. Never raises."""
    ident = SiteIdentity(domain=domain)
    try:
        client = http or httpx
        r = client.get(f"https://{domain}/", timeout=timeout,
                       follow_redirects=True, headers={"User-Agent": _UA})
        ident.final_host = str(r.url.host or "")
        body = r.text[:_FETCH_CAP_BYTES]
        if m := _TITLE_RE.search(body):
            ident.title = re.sub(r"\s+", " ", m.group(1)).strip()[:200]
        if m := _META_RE.search(body):
            ident.site_name = m.group(1).strip()
        if m := _CANONICAL_RE.search(body):
            host = re.sub(r"^https?://", "", m.group(1)).split("/")[0]
            ident.canonical_host = host.lower()
    except Exception as e:  # noqa: BLE001 — verification must never break a sync
        ident.error = f"{type(e).__name__}: {str(e)[:120]}"
    return ident


def verify_same_company(name_a: str, domain_a: str, name_b: str, domain_b: str,
                        *, adjudicate=None, fetch=fetch_site_identity) -> Verdict:
    """Decide whether two (name, domain) records are the same organization."""
    ra, rb = registrable_domain(domain_a), registrable_domain(domain_b)
    if not ra or not rb:
        return Verdict("unknown", "low", "missing-domain")
    if ra == rb:
        return Verdict("same", "high", "registrable-equal", [ra])

    a, b = fetch(ra), fetch(rb)
    if a.error and b.error:
        return Verdict("unknown", "low", "both-fetches-failed",
                       [a.error, b.error])

    fa = registrable_domain(a.final_host) or ""
    fb = registrable_domain(b.final_host) or ""
    if fa and fa == fb:
        return Verdict("same", "high", "redirect-convergence", [fa])
    ca = registrable_domain(a.canonical_host) or ""
    cb = registrable_domain(b.canonical_host) or ""
    if (ca and ca == rb) or (cb and cb == ra) or (ca and ca == cb):
        return Verdict("same", "high", "cross-canonical", [ca or cb])
    if fa == rb or fb == ra:
        return Verdict("same", "high", "redirect-into", [fa or fb])

    if adjudicate is not None:
        try:
            call = adjudicate(name_a, a, name_b, b)
            if call in ("same", "different"):
                return Verdict(call, "low", "adjudicated",
                               [a.title, b.title])
        except Exception as e:  # noqa: BLE001
            logger.warning("site-verify adjudication failed: %s", e)
    return Verdict("unknown", "low", "no-deterministic-signal",
                   [a.title, b.title])


# ── verdict cache (engagement settings JSON) ─────────────────────────────


def _load(repo) -> dict:
    try:
        return json.loads(repo.get_setting(_CACHE_KEY) or "{}")
    except Exception:  # noqa: BLE001
        return {}


def cached_verdict(repo, dom_a: str | None, dom_b: str | None) -> Verdict | None:
    row = _load(repo).get(pair_key(dom_a, dom_b))
    if not row:
        return None
    return Verdict(row.get("verdict", "unknown"), row.get("confidence", "low"),
                   row.get("method", "cached"), row.get("evidence") or [])


def store_verdict(repo, dom_a: str | None, dom_b: str | None, v: Verdict, *,
                  decided_by: str = "auto") -> None:
    """Persist a verdict. Human rows are permanent — auto never overwrites."""
    state = _load(repo)
    key = pair_key(dom_a, dom_b)
    prior = state.get(key)
    if prior and prior.get("decided_by") == "human" and decided_by != "human":
        return
    state[key] = {"verdict": v.verdict, "confidence": v.confidence,
                  "method": v.method, "evidence": v.evidence[:4],
                  "decided_by": decided_by,
                  "decided_at": datetime.now(UTC).isoformat()}
    try:
        repo.set_setting(_CACHE_KEY, json.dumps(state))
    except Exception:  # noqa: BLE001
        logger.warning("site-verify verdict store failed (continuing)")


def verified_same_pairs(repo) -> set[str]:
    """Pair keys with a high-confidence 'same' verdict — the ONLY set that may
    relax a domain-conflict gate (low-confidence 'same' stays review-only)."""
    return {k for k, row in _load(repo).items()
            if row.get("verdict") == "same"
            and (row.get("confidence") == "high"
                 or row.get("decided_by") == "human")}


def verified_different_pairs(repo) -> set[str]:
    return {k for k, row in _load(repo).items()
            if row.get("verdict") == "different"}
