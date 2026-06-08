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

logger = logging.getLogger(__name__)

_BASE = "https://api.apify.com/v2/acts"
_ACTOR_POSTS = "harvestapi~linkedin-profile-posts"
_ACTOR_ENRICH = "freshdata~fresh-linkedin-profile-data"

# run-sync can be slow (a multi-profile scrape runs minutes); the endpoint itself
# caps a sync run at ~300s, so match that and let the caller retry/treat a timeout
# as an empty pull rather than a crash.
_TIMEOUT_S = 300.0


class ApifyError(RuntimeError):
    """An Apify actor run failed or timed out."""


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
        "city": data.get("city"),
        "country": data.get("country"),
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
            raise ApifyError(f"{actor} → HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            data = resp.json()
        except ValueError as e:  # 200 with a non-JSON/truncated body
            raise ApifyError(f"{actor} returned non-JSON: {resp.text[:200]}") from e
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


async def enrich(linkedin_url: str, *, client: httpx.AsyncClient | None = None) -> dict | None:
    """Enrich one LinkedIn profile URL → company + firmographics, or None."""
    items = await _run_actor(_ACTOR_ENRICH, {"linkedin_url": linkedin_url}, client=client)
    return normalize_enrichment(items)


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
