"""Apify connectors for LinkedIn social listening — posts/engagement + enrichment.

Two actors (validated against live output):
  - harvestapi~linkedin-profile-posts: scrape a profile/company's posts plus each
    reaction and comment. The dataset is a FLAT list of items typed
    'post' | 'reaction' | 'comment'; reaction/comment items carry
    actor.{name, position, linkedinUrl} — `position` is the free title we filter on.
  - freshdata~fresh-linkedin-profile-data: enrich one profile URL → full_name,
    job_title, company, company_domain (a REAL domain), industry, employee_count.
    Resolves the URN-style URLs the post scraper returns.

Cost pattern: the post scrape is cheap and already carries the title, so we filter
to decision-makers on it for free and only pay to enrich the survivors.

Parsing (`parse_engagers`, `normalize_enrichment`) is pure and unit-tested against
the real shapes; the HTTP (`_run_actor`) is the only side-effecting part.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from pydantic import BaseModel

from auto_search.clients.upstream import UpstreamError, UpstreamQuotaError, is_quota

logger = logging.getLogger(__name__)

_BASE = "https://api.apify.com/v2/acts"
_ACTOR_POSTS = "harvestapi~linkedin-profile-posts"
_ACTOR_ENRICH = "freshdata~fresh-linkedin-profile-data"

# run-sync can be slow (a multi-profile scrape runs minutes); the endpoint itself
# caps a sync run at ~300s, so match that and let the caller retry/treat a timeout
# as an empty pull rather than a crash.
_TIMEOUT_S = 300.0


class ApifyError(UpstreamError):
    """An Apify actor run failed or timed out."""


class ApifyQuotaExceeded(ApifyError, UpstreamQuotaError):
    """The Apify ACCOUNT is capped (monthly usage hard limit / out of credits).

    Deliberately distinct from ApifyError: a per-target `except ApifyError:
    continue` is right for one bad profile and catastrophic for an account-wide
    cap — on 2026-07-27 that swallow turned a total outage into a green run."""


class RawEngager(BaseModel):
    """One reaction/comment from the post scraper, before any filtering/enrichment.

    `position` is LinkedIn's free-text headline — rich enough for the decision-maker
    filter, so we never enrich a junior liker. `linkedin_url` (often a URN form)
    is what we feed the enrichment actor for the survivors."""

    name: str
    position: str | None = None          # LinkedIn headline / title — the filter input
    linkedin_url: str | None = None
    engagement_type: str = "like"         # like | comment
    reaction_type: str | None = None
    comment_text: str | None = None
    post_url: str | None = None
    post_title: str | None = None


def _post_index(items: list[dict]) -> dict[str, dict]:
    """Map postId -> the post item, so a reaction/comment can borrow its url+text."""
    out: dict[str, dict] = {}
    for it in items:
        if it.get("type") == "post":
            pid = str(it.get("id") or it.get("postId") or "")
            if pid:
                out[pid] = it
    return out


def parse_engagers(items: list[dict]) -> list[RawEngager]:
    """Flatten an Apify posts dataset into one RawEngager per reaction/comment.

    The dataset mixes 'post', 'reaction' and 'comment' items; we read the latter
    two and attach their parent post's url/title (looked up by postId) for context.
    """
    posts = _post_index(items)
    out: list[RawEngager] = []
    for it in items:
        kind = it.get("type")
        if kind not in ("reaction", "comment"):
            continue
        actor = it.get("actor") or {}
        name = (actor.get("name") or "").strip()
        if not name:
            continue
        post = posts.get(str(it.get("postId") or "")) or {}
        out.append(RawEngager(
            name=name,
            position=actor.get("position") or actor.get("info"),
            linkedin_url=actor.get("linkedinUrl") or actor.get("url"),
            engagement_type="comment" if kind == "comment" else "like",
            reaction_type=it.get("reactionType"),
            comment_text=it.get("commentary") if kind == "comment" else None,
            post_url=post.get("linkedinUrl") or post.get("url"),
            post_title=(post.get("content") or post.get("text") or None),
        ))
    return out


def _loc_str(v: object) -> str | None:
    """Location fields sometimes arrive as objects ({'name': 'United States', …})
    instead of plain strings (actor schema drift, 2026-07-07 crash) — normalize
    to a string or None so downstream filters always get text."""
    if isinstance(v, str):
        return v or None
    if isinstance(v, dict):
        for k in ("name", "text", "default", "value"):
            if isinstance(v.get(k), str) and v[k]:
                return v[k]
    return None


def normalize_enrichment(items: list[dict]) -> dict | None:
    """Pull the fields we need from a Fresh-LinkedIn-Profile-Data result.

    Returns None when the profile didn't resolve (empty, or a non-dict/error
    item), so the caller can skip cleanly rather than crash.
    """
    if not items or not isinstance(items[0], dict):
        return None
    rec = items[0]
    data = rec.get("data") if isinstance(rec.get("data"), dict) else rec
    full_name = data.get("full_name") or " ".join(
        x for x in (data.get("first_name"), data.get("last_name")) if x)
    if not (full_name or data.get("company")):
        return None
    return {
        "full_name": full_name or "",
        "job_title": data.get("job_title") or data.get("headline"),
        "company": data.get("company"),
        "company_domain": data.get("company_domain") or data.get("company_website"),
        "industry": data.get("company_industry"),
        "employee_count": data.get("company_employee_count"),
        "linkedin_url": data.get("linkedin_url"),
        "city": _loc_str(data.get("city")),
        "country": _loc_str(data.get("country")),
    }


def _token() -> str:
    token = os.getenv("APIFY_API_KEY")
    if not token:
        raise ApifyError("APIFY_API_KEY is not set")
    return token


async def _run_actor(actor: str, payload: dict, *, client: httpx.AsyncClient | None = None) -> list[dict]:
    """Run an actor synchronously and return its dataset items."""
    url = f"{_BASE}/{actor}/run-sync-get-dataset-items"
    params = {"token": _token()}
    owns = client is None
    client = client or httpx.AsyncClient(timeout=_TIMEOUT_S)
    try:
        resp = await client.post(url, params=params, json=payload)
        if resp.status_code >= 400:
            # A capped account gets its OWN class so per-target handlers can't
            # swallow it as "one bad profile" (2026-07-27 hard-limit outage).
            cls = ApifyQuotaExceeded if is_quota(resp.status_code, resp.text) else ApifyError
            raise cls(f"{actor} → HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            data = resp.json()
        except ValueError as e:  # 200 with a non-JSON/truncated body
            raise ApifyError(f"{actor} returned non-JSON: {resp.text[:200]}") from e
        if isinstance(data, dict) and data.get("error"):
            cls = ApifyQuotaExceeded if is_quota(200, data) else ApifyError
            raise cls(f"{actor} → error body: {str(data['error'])[:300]}")
        if isinstance(data, list):
            return data
        return data.get("items", []) if isinstance(data, dict) else []
    except httpx.HTTPError as e:
        raise ApifyError(f"{actor} request failed: {e}") from e
    finally:
        if owns:
            await client.aclose()


async def fetch_engagers(
    profile_urls: list[str],
    *,
    max_posts: int = 10,
    max_reactions: int = 50,    # per post — bounded (0 would mean ALL, a viral-post footgun)
    max_comments: int = 25,     # per post — bounded
    posted_limit_date: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[RawEngager]:
    """Scrape recent posts for the given profiles and return their engagers.

    `max_reactions`/`max_comments` are per-post ceilings: each reaction/comment is
    a billed Apify item, so leaving them at 0 (=ALL) lets one viral post run up an
    unbounded bill. They default bounded; raise deliberately if you need more.
    """
    if not profile_urls:
        return []
    payload: dict[str, Any] = {
        "targetUrls": profile_urls,
        "scrapeReactions": True,
        "scrapeComments": True,
        "maxPosts": max_posts,
        "maxReactions": max_reactions,
        "maxComments": max_comments,
    }
    if posted_limit_date:
        payload["postedLimitDate"] = posted_limit_date
    items = await _run_actor(_ACTOR_POSTS, payload, client=client)
    engagers = parse_engagers(items)
    logger.info("apify posts: %d profiles → %d items → %d engagers",
                len(profile_urls), len(items), len(engagers))
    return engagers


_ACTOR_PROFILE = "harvestapi~linkedin-profile-scraper"


def normalize_profile(items: list[dict]) -> dict | None:
    """Pull the fields we need from a harvestapi linkedin-profile-scraper result.

    Unlike the freshdata actor, this RESOLVES LinkedIn's obfuscated `ACoAAA…` member
    URLs (what the post-reactions scraper returns) into the public profile, and gives
    the *current* company + the public slug. Returns None if the profile didn't resolve.
    Same key shape as `normalize_enrichment` so the caller is unchanged.
    """
    if not items or not isinstance(items[0], dict):
        return None
    p = items[0]
    pos = p.get("currentPosition") or p.get("experience") or []
    cur = pos[0] if pos and isinstance(pos[0], dict) else {}
    first = (p.get("firstName") or "").strip()
    last = (p.get("lastName") or "").strip()
    full_name = (p.get("fullName") or f"{first} {last}").strip()
    company = cur.get("companyName")
    if not (full_name or company):
        return None
    emails = [e for e in (p.get("emails") or []) if isinstance(e, str) and e]
    return {
        "full_name": full_name,
        "job_title": cur.get("position") or p.get("headline"),
        "company": company,
        "company_domain": None,            # this actor returns the company LinkedIn URL, not a website
        "company_linkedin": cur.get("companyLinkedinUrl"),
        "industry": None,
        "employee_count": None,
        "linkedin_url": p.get("linkedinUrl"),    # the RESOLVED public slug (not the ACoAAA id)
        "email": emails[0] if emails else None,
        "city": _loc_str(p.get("location")),
        "country": None,
    }


async def enrich(linkedin_url: str, *, client: httpx.AsyncClient | None = None) -> dict | None:
    """Enrich one LinkedIn profile URL → resolved public profile + current company, or
    None. Uses harvestapi's profile scraper, which accepts LinkedIn's obfuscated
    `ACoAAA…` member URLs — the freshdata actor returns nothing for those, which is why
    the TOFU runner was dropping every reactor at 'no company'.

    KNOWN GAP: harvestapi returns country=None (normalize_profile above), so callers
    that hard-gate on country (poll_events' is_us) must NOT use this — see
    enrich_freshdata below (2026-07-23 audit)."""
    items = await _run_actor(_ACTOR_PROFILE, {"queries": [linkedin_url]}, client=client)
    return normalize_profile(items)


async def enrich_freshdata(linkedin_url: str, *,
                           client: httpx.AsyncClient | None = None) -> dict | None:
    """Enrich one PUBLIC /in/<slug> profile URL via freshdata → company + COUNTRY.

    Why this exists next to enrich() (2026-07-23 audit): the Jun 30 swap to
    harvestapi (ec25c9f — needed because freshdata can't resolve `ACoAAA…`
    reactor URNs) returns country=None, so poll.py's is_us() gate silently
    dropped EVERY event attendee for 3+ weeks. Event-post AUTHORS come from the
    post-search actor with public /in/ slugs — the URN limitation never applied
    to them — so the event path uses freshdata (which returns country/city);
    reactor/target paths keep harvestapi. This is the pre-ec25c9f enrich body."""
    items = await _run_actor(_ACTOR_ENRICH, {"linkedin_url": linkedin_url}, client=client)
    return normalize_enrichment(items)


# Reactions (likes) on a specific post — the LinkedIn TOFU ad-engagement flow. A
# different actor than the profile-posts scraper: it takes post URLs directly and
# returns a flat list of reactions, each with the reactor's actor + the postId.
_ACTOR_REACTIONS = "harvestapi~linkedin-post-reactions"


async def fetch_post_reactions(post_url: str, *, max_items: int = 50,
                               client: httpx.AsyncClient | None = None) -> list[dict]:
    """People who reacted to one post: [{name, position, linkedin_url, profile_id,
    reaction_type}]. `max_items` caps reactions per post (0 = all, a viral-post
    footgun — keep it bounded)."""
    items = await _run_actor(_ACTOR_REACTIONS,
                             {"posts": [post_url], "maxItems": max_items}, client=client)
    out: list[dict] = []
    for it in items:
        actor = it.get("actor") or {}
        name = (actor.get("name") or "").strip()
        if not name:
            continue
        out.append({
            "name": name,
            "position": actor.get("position") or actor.get("info"),
            "linkedin_url": actor.get("linkedinUrl") or actor.get("url"),
            "profile_id": actor.get("id"),
            "reaction_type": it.get("reactionType"),
        })
    return out


# ── event keyword search (datadoping~linkedin-posts-search-scraper) ──────────
# Search public LinkedIn posts by keyword (e.g. an event hashtag "HIMSS26"). Each
# result carries the post text + author — we read the TEXT to confirm the author
# (a person) actually attended, then enrich + qualify that attendee.
_ACTOR_POST_SEARCH = "datadoping~linkedin-posts-search-scraper"

# date_filter window → the actor's enum. Manual runs widen the window; the cron
# stays past-24h.
DATE_WINDOWS = {"24h": "past-24h", "week": "past-week", "month": "past-month"}


class EventPost(BaseModel):
    """One public post returned by a keyword search, with its author."""

    author_name: str
    author_headline: str | None = None
    author_url: str | None = None        # /in/<slug> for a person, /company//showcase/ for an org
    post_url: str | None = None
    text: str = ""
    keyword: str | None = None           # which search keyword surfaced it

    @property
    def author_is_person(self) -> bool:
        """A real person's profile (/in/), not a company/showcase page — only a
        person can 'attend'."""
        return "/in/" in (self.author_url or "").lower()


def parse_event_posts(items: list[dict]) -> list[EventPost]:
    """Flatten the post-search dataset into EventPost rows (pure, unit-tested)."""
    out: list[EventPost] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        author = it.get("author") or {}
        name = (author.get("name") or it.get("owner_name") or "").strip()
        if not name:
            continue
        text = it.get("text")
        out.append(EventPost(
            author_name=name,
            author_headline=author.get("headline") or author.get("occupation"),
            author_url=author.get("profile_url") or author.get("linkedinUrl"),
            post_url=it.get("post_url") or it.get("url"),
            text=text if isinstance(text, str) else "",
            keyword=(it.get("input") or {}).get("keyword") if isinstance(it.get("input"), dict) else None,
        ))
    return out


async def search_event_posts(
    keywords: list[str],
    *,
    max_posts: int = 25,
    date_filter: str = "past-24h",
    sort_by: str = "date_posted",
    client: httpx.AsyncClient | None = None,
) -> list[EventPost]:
    """Search public posts for the given event keywords. `date_filter` is the
    actor enum (past-24h | past-week | past-month) — the cron uses 24h, a manual
    run can widen it."""
    keywords = [k for k in (keywords or []) if k and k.strip()]
    if not keywords:
        return []
    items = await _run_actor(_ACTOR_POST_SEARCH, {
        "keywords": keywords, "max_posts": max(10, max_posts),
        "sort_by": sort_by, "date_filter": date_filter,
    }, client=client)
    posts = parse_event_posts(items)
    logger.info("apify post-search: %d keywords → %d posts", len(keywords), len(posts))
    return posts
