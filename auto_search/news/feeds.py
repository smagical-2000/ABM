"""Fetch RCM / healthcare-regulation headlines from Google News RSS (free).

Google News exposes an RSS feed for any search query, so a handful of tight
topic queries gives broad, reliable coverage with zero cost and no API key. We
keep only title / source / date / link (Google's description is just the title
again); the real value-add is the enrich pass's "why it matters". Publication
feeds (Becker's, Fierce, Healthcare Dive) can be layered in later for richer
snippets — the parser already returns NewsItem either way.
"""

from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from auto_search.news.models import NewsItem

logger = logging.getLogger(__name__)

_GOOGLE_NEWS = "https://news.google.com/rss/search"
_PARAMS = {"hl": "en-US", "gl": "US", "ceid": "US:en"}
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# One tight query per topic. Quoted phrases keep it on-RCM; the OR-clauses widen
# coverage without drifting into generic health/clinical news.
QUERIES = {
    "prior_auth": '"prior authorization" healthcare (CMS OR payer OR rule OR denial OR automation)',
    "denials": '("claim denials" OR "denials management") healthcare "revenue cycle"',
    "rcm_ai": '"revenue cycle" healthcare (automation OR AI OR "artificial intelligence" OR agentic)',
    "eligibility": '("eligibility verification" OR "insurance verification" OR "benefits verification") healthcare',
    "policy": 'CMS healthcare (rule OR regulation OR "final rule" OR mandate) (billing OR claims OR "prior authorization" OR reimbursement)',
}

_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: str | None) -> str:
    return _TAG_RE.sub("", html.unescape(text or "")).strip()


def _to_iso(rfc822: str | None) -> str | None:
    if not rfc822:
        return None
    try:
        return parsedate_to_datetime(rfc822).astimezone(UTC).isoformat()
    except (TypeError, ValueError):
        return None


def _parse(xml_text: str, topic: str, now: str) -> list[NewsItem]:
    items: list[NewsItem] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.warning("news RSS parse failed for %s: %s", topic, e)
        return items
    for it in root.iterfind(".//item"):
        title = _clean(it.findtext("title"))
        link = (it.findtext("link") or "").strip()
        if not title or not link:
            continue
        src_el = it.find("source")
        source = (src_el.text or "").strip() if src_el is not None else None
        # Google appends " - Publication" to titles; drop it (we show source separately).
        if source and title.endswith(f" - {source}"):
            title = title[: -(len(source) + 3)].strip()
        items.append(NewsItem(
            url=link, title=title, source=source or None,
            published_at=_to_iso(it.findtext("pubDate")),
            snippet=(_clean(it.findtext("description")) or None),
            topic=topic, fetched_at=now,
        ))
    return items


async def fetch_all(*, max_per_query: int = 15, timeout: float = 20.0) -> list[NewsItem]:
    """Pull every topic query, deduped by URL across topics. Resilient: one
    failing query doesn't sink the rest."""
    now = datetime.now(UTC).isoformat()
    out: list[NewsItem] = []
    seen: set[str] = set()
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True,
                                 headers={"User-Agent": _UA}) as client:
        for topic, query in QUERIES.items():
            try:
                resp = await client.get(_GOOGLE_NEWS, params={"q": query, **_PARAMS})
                resp.raise_for_status()
            except Exception as e:  # noqa: BLE001 — one query must not kill the pull
                logger.warning("news fetch failed for %s: %s", topic, e)
                continue
            for item in _parse(resp.text, topic, now)[:max_per_query]:
                if item.url in seen:
                    continue
                seen.add(item.url)
                out.append(item)
    logger.info("news: fetched %d unique headlines across %d topics", len(out), len(QUERIES))
    return out
