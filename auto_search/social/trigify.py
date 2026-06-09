"""Adapt Trigify's raw workflow payload into our clean `Engager`.

Trigify's `person_enrichment` result is nested and snake_case
(`{data:{prospect:{...}}}` or `{prospect:{...}}`), and a workflow author
shouldn't have to hand-wire eight brittle variable refs to the exact nesting
level. So the http_request step sends the WHOLE enrichment result under one
`enrichment` ref plus a little context, and we pull the fields out here —
tolerant to the nesting depth, to camelCase aliases, and to `enrichment`
arriving as a JSON string. If `enrichment` is absent we read flat top-level
fields, so a hand-built/flat payload still works.

This keeps `Engager` clean and puts all of Trigify's shape-quirks in one place
that's unit-tested.
"""

from __future__ import annotations

import json
from typing import Any

from auto_search.social.models import Engager

_VALID_SOURCES = {"magical_post", "competitor_post", "event"}


def _as_dict(v: object) -> dict:
    """A dict from a dict, or from a JSON string, else empty."""
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and v.strip():
        try:
            d = json.loads(v)
        except (ValueError, TypeError):
            return {}
        return d if isinstance(d, dict) else {}
    return {}


def _prospect(enrichment: object) -> dict:
    """Find the person fields wherever Trigify nested them.

    Handles {prospect:{...}}, {data:{prospect:{...}}}, and an already-flat dict.
    """
    e = _as_dict(enrichment)
    if isinstance(e.get("data"), dict):
        e = e["data"]
    if isinstance(e.get("prospect"), dict):
        return e["prospect"]
    return e


def _first(d: dict, *keys: str) -> Any:
    """First present, non-empty value among `keys` (camel/snake aliases)."""
    for k in keys:
        v = d.get(k)
        if v not in (None, "", []):
            return v
    return None


def _source(raw: object) -> str:
    """Normalize the workflow-supplied source; default to the safe, gated
    'magical_post' (an unknown source must never silently skip the event
    attendance gate)."""
    s = str(raw or "").strip().lower()
    return s if s in _VALID_SOURCES else "magical_post"


def _engagement_type(raw: object) -> str:
    s = str(raw or "like").strip().lower()
    return s if s in ("like", "comment") else "like"


def engager_from_trigify(payload: dict) -> Engager:
    """Build an Engager from a Trigify http_request payload (enrichment + context)."""
    p = _prospect(payload.get("enrichment"))

    full_name = _first(p, "full_name", "fullName") or _first(payload, "full_name", "fullName")
    if not full_name:
        first = _first(p, "first_name", "firstName")
        last = _first(p, "last_name", "lastName")
        full_name = " ".join(str(x) for x in (first, last) if x)

    return Engager(
        full_name=full_name or "",
        job_title=_first(p, "job_title", "jobTitle") or _first(payload, "job_title", "jobTitle"),
        job_title_levels=_first(p, "job_title_levels", "jobTitleLevels") or [],
        job_title_role=_first(p, "job_title_role", "jobTitleRole"),
        company_name=(_first(p, "job_company_name", "companyName", "currentCompanyName")
                      or _first(payload, "company_name", "companyName")),
        company_website=(_first(p, "job_company_website", "companyWebsite", "companyDomain")
                         or _first(payload, "company_website", "companyWebsite")),
        industry=_first(p, "industry", "companyIndustry"),
        linkedin_url=(_first(p, "linkedin_url", "linkedinUrl")
                      or _first(payload, "linkedin_url", "profile_url", "profileUrl")),
        source=_source(payload.get("source")),
        engagement_type=_engagement_type(payload.get("engagement_type")),
        reaction_type=_first(payload, "reaction_type", "reactionType"),
        post_url=_first(payload, "post_url", "postUrl"),
        post_title=_first(payload, "post_title", "postTitle", "postText"),
        comment_text=_first(payload, "comment_text", "commentText"),
        event_name=_first(payload, "event_name", "eventName"),
        engaged_at=payload.get("engaged_at"),
    )
