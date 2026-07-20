"""Exa client — web retrieval for the AE one-off account lookup.

One endpoint only: POST /search with page-text contents, used by the lookup
resolver to identify a company from its name/website before we spend real
money scoring it. This is NOT a general research tool — deep research stays
on the scorer's Claude web_search path so one-off scores match batch scores.

COST SAFETY
-----------
Exa bills per search (~$5/1k) plus per result whose page text is returned
(~$1/1k). A resolve is one search x <=5 results ≈ $0.01. `search_cost()` is
the estimate the caller records as a cost event so lookup spend is auditable
like every other paid step. Hard ceiling on num_results keeps a bad caller
from turning retrieval into a bill.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://api.exa.ai/search"
_TIMEOUT_S = 25
MAX_RESULTS = 8                       # hard ceiling; resolve uses 5
_TEXT_CHARS = 1200                    # page-text snippet per result

# Published pricing, kept slightly conservative: $5/1k searches, $1/1k pages.
_COST_PER_SEARCH = 0.005
_COST_PER_PAGE_TEXT = 0.001


class ExaError(RuntimeError):
    """Search failed (auth, quota, network, or a malformed response)."""


class ExaResult(BaseModel):
    """One web hit, trimmed to what the resolver needs."""

    title: str = ""
    url: str = ""
    domain: str = ""                  # bare host, www-stripped (dedup/compare key)
    text: str = ""                    # leading page text (<=_TEXT_CHARS chars)
    published: str | None = None


def search_cost(num_results: int) -> float:
    """Estimated $ for one search returning `num_results` page texts."""
    return round(_COST_PER_SEARCH + max(0, num_results) * _COST_PER_PAGE_TEXT, 4)


def domain_of(url: str | None) -> str:
    """Bare lowercase host of a URL ('https://www.ivyrehab.com/x' -> 'ivyrehab.com')."""
    raw = (url or "").strip().lower()
    if not raw:
        return ""
    host = urlparse(raw if "://" in raw else f"https://{raw}").netloc or ""
    host = host.split(":")[0].removeprefix("www.")
    return host if "." in host else ""


def search(query: str, *, num_results: int = 5,
           api_key: str | None = None) -> list[ExaResult]:
    """One Exa search with page text. Raises ExaError on any failure —
    the resolver degrades to manual entry, it never guesses blind."""
    key = api_key or os.getenv("EXA_API_KEY")
    if not key:
        raise ExaError("EXA_API_KEY not set")
    n = max(1, min(int(num_results), MAX_RESULTS))
    body = {
        "query": query,
        "numResults": n,
        "type": "auto",
        "contents": {"text": {"maxCharacters": _TEXT_CHARS}},
    }
    try:
        resp = httpx.post(_SEARCH_URL, json=body, timeout=_TIMEOUT_S,
                          headers={"x-api-key": key, "Content-Type": "application/json"})
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as e:
        raise ExaError(f"Exa search failed: HTTP {e.response.status_code}") from e
    except Exception as e:  # noqa: BLE001 — network/JSON; one error type upstream
        raise ExaError(f"Exa search failed: {type(e).__name__}: {e}") from e

    rows = data.get("results")
    if not isinstance(rows, list):
        raise ExaError("Exa search returned no results list")
    out: list[ExaResult] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        url = str(r.get("url") or "")
        out.append(ExaResult(
            title=str(r.get("title") or ""),
            url=url,
            domain=domain_of(url),
            text=str(r.get("text") or "")[:_TEXT_CHARS],
            published=r.get("publishedDate"),
        ))
    logger.info("exa search %r -> %d results", query[:80], len(out))
    return out
