"""Poll monitored LinkedIn accounts for decision-maker engagers — the Apify flow.

The cost-shaped pipeline (cheapest first, so credits are spent only where they
buy a real lead):

    1. scrape recent posts + engagers for the active targets   (Apify, cheap)
    2. dedup engagers by profile URL across all posts            (free)
    3. decision-maker filter on the scraped `position` headline  (free)
    4. drop anyone whose headline says they work at Magical      (free)
    5. enrich ONLY the survivors → real company + domain         (Apify, paid, capped)
    6. hand the enriched Engager to the shared `ingest_engager`   (LLM qualify, gated)

Steps 1 and 5 are the only paid Apify calls; everything between is free filtering,
so a 40-like post costs ~1 scrape + the handful of decision-makers' enrichments,
not 40 enrichments.
"""

from __future__ import annotations

import logging
import os
import re

from auto_search.normalize import normalize_company_name
from auto_search.scoring import spend_guard
from auto_search.social import apify
from auto_search.social.filters import is_attending, is_us
from auto_search.social.ingest import ingest_engager
from auto_search.social.models import Engager, SocialTarget, source_for_kind
from auto_search.social.seniority import is_decision_maker

logger = logging.getLogger(__name__)

# Employer parsed from a LinkedIn headline: the text after the LAST " at "/" @ "
# ("VP of Sales at Acme" → "Acme"), cut at the first clause separator
# ("COO at Outera | Advisor" → "Outera"). Conservative — only "at"/"@" (not
# "of", which mis-grabs "Head of Marketing"); a headline with neither yields
# None and falls back to enrich-first so we don't lose "Founder of X" leads.
_HEADLINE_EMPLOYER_RE = re.compile(r".*\b(?:at|@)\s+(.+)$", re.IGNORECASE)
_HEADLINE_CUT_RE = re.compile(r"\s*[|•·;]\s*|\s+[-–—]\s+|\s+&\s+")


def company_from_headline(position: str | None) -> str | None:
    """Best-effort employer name from a headline, for an ICP check before enrich."""
    if not position:
        return None
    m = _HEADLINE_EMPLOYER_RE.search(position)
    if not m:
        return None
    company = _HEADLINE_CUT_RE.split(m.group(1).strip(), maxsplit=1)[0].strip()
    return company or None

# Conservative recorded costs (freshdata ≈ $8/1k profiles; harvestapi ≈ $2/1k items).
ENRICH_COST_USD = 0.008
SCRAPE_COST_PER_ITEM_USD = 0.002

# Per-post engager ceilings + a max number of accounts scraped per run, so the
# paid scrape can't run away (one viral post, or a long competitor list). The
# scrape is the only place spend isn't bounded by max_enrich. Env-driven so the
# small testing caps (5 each, while we validate) become a config flip at ship.
MAX_REACTIONS_PER_POST = max(1, int(os.getenv("SOCIAL_MAX_REACTIONS", "5")))
MAX_COMMENTS_PER_POST = max(1, int(os.getenv("SOCIAL_MAX_COMMENTS", "5")))
MAX_POSTS_PER_TARGET = max(1, int(os.getenv("SOCIAL_MAX_POSTS", "5")))
MAX_TARGETS_PER_RUN = 25

# Cheap pre-enrichment Magical check on the headline ("President @ Magical").
# CONSERVATIVE: only fires when Magical is the whole employer (followed by end /
# a separator, NOT another word) — so "COO at Magical Smiles Dental" (a real
# lead) is NOT dropped. The authoritative check on the enriched company still
# runs inside ingest_engager; this just avoids paying to enrich a colleague, so
# a rare false-negative only costs one enrich, while a false-positive would lose
# a real lead.
_HEADLINE_MAGICAL_RE = re.compile(r"(?:@|\bat)\s+magical\b(?!\s+[A-Za-z])", re.IGNORECASE)


def _new_summary() -> dict:
    return {"scraped": 0, "engagers": 0, "duplicates": 0, "decision_makers": 0,
            "enriched": 0, "qualified": 0, "appended": 0, "skipped": {}}


def _tally_skip(summary: dict, reason: str) -> None:
    summary["skipped"][reason] = summary["skipped"].get(reason, 0) + 1


def _tally_result(summary: dict, res) -> None:
    if res.action == "qualified":
        summary["qualified"] += 1
    elif res.action == "appended":
        summary["appended"] += 1
    elif not res.accepted:
        _tally_skip(summary, res.reason)


def _engager_from(e, enriched: dict | None, source: str) -> Engager:
    """An Engager for `e`, enriched with clean company/domain/title when available
    (falls back to the scraped headline values)."""
    enriched = enriched or {}
    return Engager(
        full_name=enriched.get("full_name") or e.name,
        job_title=enriched.get("job_title") or e.position,
        company_name=enriched.get("company"),
        company_website=enriched.get("company_domain"),
        industry=enriched.get("industry"),
        linkedin_url=enriched.get("linkedin_url") or e.linkedin_url,
        source=source,
        engagement_type=e.engagement_type,
        reaction_type=e.reaction_type,
        post_url=e.post_url,
        post_title=e.post_title,
        comment_text=e.comment_text,
    )


async def poll_targets(
    targets: list[SocialTarget],
    *,
    repo,
    op: spend_guard.Operation | None = None,
    can_qualify=None,
    abm_lookup=None,
    gate=None,
    max_posts: int = MAX_POSTS_PER_TARGET,
    posted_limit_date: str | None = None,
    max_enrich: int = 50,
    fetch_fn=apify.fetch_engagers,
    enrich_fn=apify.enrich,
    qualify_fn=None,
) -> dict:
    """Scrape → filter → enrich decision-makers → ingest. Returns a run summary.

    `max_enrich` caps paid enrichments per run (runaway-spend guard); `can_qualify`
    gates the LLM qualify inside ingest. `gate` is the shared RunControl.gate (same
    one the discovery Run button drives) — awaited before each engager so a
    pause freezes the run and a cancel stops it cleanly between engagers, never
    mid-enrichment. Both Apify calls are injectable for tests.
    """
    active = [t for t in targets if t.active and t.linkedin_url]
    if not active:
        return _new_summary()
    if len(active) > MAX_TARGETS_PER_RUN:
        logger.warning("social poll: %d active targets > cap %d — scraping the first %d",
                       len(active), MAX_TARGETS_PER_RUN, MAX_TARGETS_PER_RUN)
        active = active[:MAX_TARGETS_PER_RUN]

    # Group by kind so each engager inherits the right source/intent weight, while
    # still batching the scrape (the actor handles multiple URLs concurrently).
    by_kind: dict[str, list[str]] = {}
    for t in active:
        by_kind.setdefault(t.kind, []).append(t.linkedin_url)

    summary = _new_summary()
    seen: set[str] = set()
    enrich_count = 0

    for kind, urls in by_kind.items():
        source = source_for_kind(kind)
        summary["scraped"] += len(urls)
        try:
            engagers = await fetch_fn(urls, max_posts=max_posts,
                                      max_reactions=MAX_REACTIONS_PER_POST,
                                      max_comments=MAX_COMMENTS_PER_POST,
                                      posted_limit_date=posted_limit_date)
        except apify.ApifyError:
            logger.exception("apify post scrape failed for %s targets", kind)
            continue
        summary["engagers"] += len(engagers)
        # Meter the scrape so the budget guard sees the one line that isn't bounded
        # by max_enrich. Apify bills the POSTS too, not just reactions/comments, so
        # add a conservative post allowance (worst case maxPosts × profiles) — for a
        # cost guard, over-estimating is the safe direction.
        if op is not None and engagers:
            billed = len(engagers) + max_posts * len(urls)
            op.record(step="scrape", actual_usd=round(billed * SCRAPE_COST_PER_ITEM_USD, 4),
                      company_key=None, model="apify:linkedin-profile-posts")

        for e in engagers:
            # Cancel/pause checkpoint (shared RunControl) BEFORE any paid work on
            # this engager — a pause freezes here, a cancel stops the run cleanly.
            if gate is not None and not await gate():
                logger.info("social poll cancelled mid-run")
                summary["cancelled"] = True
                return summary
            # Dedup on the profile URL only — a name is not a safe identity
            # (two different "John Smith" reactors would collapse). An engager
            # with no captured URL can't be enriched, so skip it outright.
            ident = (e.linkedin_url or "").strip().lower()
            if not ident:
                _tally_skip(summary, "no_profile_url")
                continue
            if ident in seen:
                summary["duplicates"] += 1
                continue
            seen.add(ident)

            # FREE filters first — never pay to enrich a junior liker or a colleague.
            if not is_decision_maker(e.position)[0]:
                _tally_skip(summary, "not_decision_maker")
                continue
            if e.position and _HEADLINE_MAGICAL_RE.search(e.position):
                _tally_skip(summary, "magical_employee")
                continue
            summary["decision_makers"] += 1
            kw = {"repo": repo, "op": op, "can_qualify": can_qualify, "abm_lookup": abm_lookup}
            if qualify_fn is not None:
                kw["qualify_fn"] = qualify_fn
            budget = max_enrich - enrich_count

            company = company_from_headline(e.position)
            if not company:
                # No employer in the headline → must enrich to learn the company,
                # THEN qualify (keys under the enriched name; preserves "Founder of
                # X" leads). The only path that pays enrich before the ICP gate.
                enriched = await _enrich_and_record(enrich_fn, e.linkedin_url, op, summary, budget)
                if enriched is None:
                    continue
                enrich_count += 1
                if not enriched.get("company"):
                    _tally_skip(summary, "enrich_no_company")
                    continue
                _tally_result(summary, await ingest_engager(
                    _engager_from(e, enriched, source), **kw))
                continue

            # Headline names an employer → ICP-CHECK FIRST (cheap), enrich only if
            # it's a fit, so we never pay to enrich a person at a non-ICP company.
            key = normalize_company_name(company)
            known = repo.get(key)
            if known is not None:
                # Already decided in a prior run: never re-qualify, and never
                # attach a contact to a non-ICP company (keeps the writes and the
                # not_icp counter in agreement — no silent append-then-reject).
                if known.get("icp_status") not in ("qualified", "needs_review"):
                    _tally_skip(summary, "not_icp")
                    continue
                probe = _engager_from(e, None, source).model_copy(update={"company_name": company})
                if not repo.add_signal(key, probe.to_signal()):
                    summary["duplicates"] += 1     # seen this exact engagement → don't re-enrich
                    continue
                summary["appended"] += 1
            else:
                # New company → qualify on the headline name. ingest saves the
                # verdict (caching a rejection so we never re-qualify it) and
                # attaches the scrape-level contact.
                res = await ingest_engager(_engager_from(e, None, source).model_copy(
                    update={"company_name": company}), **kw)
                if res.action != "qualified":      # gate refused (budget/cap)
                    _tally_skip(summary, res.reason)
                    continue
                if res.reason not in ("qualified", "needs_review"):
                    _tally_skip(summary, "not_icp")
                    continue
                summary["qualified"] += 1

            # ICP company + a freshly-attached contact → enrich it (capped) and
            # replace the scrape-level signal in place with clean company/title.
            enriched = await _enrich_and_record(enrich_fn, e.linkedin_url, op, summary, budget)
            if enriched is None:
                continue                            # cap hit / error — contact still saved
            enrich_count += 1
            if enriched.get("company"):
                # Keep the scrape profile URL as identity so this REPLACES the
                # just-attached signal (same source_external_id), not duplicates it.
                repo.update_signal(key, _engager_from(
                    e, {**enriched, "linkedin_url": e.linkedin_url}, source).to_signal())

    logger.info("social poll: %s", summary)
    return summary


def _new_event_summary() -> dict:
    return {"keywords": 0, "posts": 0, "attendees": 0, "enriched": 0,
            "qualified": 0, "appended": 0, "skipped": {}}


async def poll_events(
    keywords: list[str],
    *,
    repo,
    op: spend_guard.Operation | None = None,
    can_qualify=None,
    abm_lookup=None,
    gate=None,
    date_filter: str = "past-24h",
    max_posts: int = 25,
    max_enrich: int = 50,
    search_fn=apify.search_event_posts,
    enrich_fn=apify.enrich,
    qualify_fn=None,
) -> dict:
    """Find event ATTENDEES from a keyword post search and run them through ICP.

    The cost-shaped gauntlet (cheapest first), per the product rule "confirm the
    author actually attended before extracting":

      1. keyword search for recent posts                      (Apify, cheap)
      2. author must be a PERSON (not the event's own page)   (free)
      3. the post TEXT must confirm attendance (verb, not     (free)
         just topic commentary)
      4. decision-maker by headline                           (free)
      5. enrich the attendee → company + country              (Apify, paid, capped)
      6. must be US-based                                     (free, on enriched)
      7. ICP-qualify the company → panel (event_attendance)   (LLM, gated)
    """
    kws = [k for k in (keywords or []) if k and k.strip()]
    summary = _new_event_summary()
    summary["keywords"] = len(kws)
    if not kws:
        return summary
    try:
        posts = await search_fn(kws, max_posts=max_posts, date_filter=date_filter)
    except apify.ApifyError:
        logger.exception("apify event search failed")
        return summary
    summary["posts"] = len(posts)
    if op is not None and posts:
        op.record(step="event_search", company_key=None, model="apify:posts-search",
                  actual_usd=round(len(posts) * SCRAPE_COST_PER_ITEM_USD, 4))

    seen: set[str] = set()
    enrich_count = 0
    kw_label = kws[0].strip('"') if len(kws) == 1 else None

    for p in posts:
        if gate is not None and not await gate():
            summary["cancelled"] = True
            return summary
        ident = (p.author_url or "").strip().lower()
        if not p.author_is_person or not ident:
            _tally_skip(summary, "not_a_person")
            continue
        if ident in seen:
            summary["duplicates"] = summary.get("duplicates", 0) + 1
            continue
        seen.add(ident)
        # FREE gate: the post text must confirm the author actually attended
        # (a verb/recap, not just the event name). We do NOT decision-maker-filter
        # on the post HEADLINE here — that's a freeform tagline ("Healthcare AI
        # leader…"), not a job title, and was dropping real decision-makers. The
        # authoritative title comes from enrichment, so the decision-maker check
        # runs inside ingest_engager on the enriched job_title.
        if not is_attending(p.text, p.text)[0]:
            _tally_skip(summary, "attendance_unconfirmed")
            continue
        summary["attendees"] += 1

        enriched = await _enrich_and_record(enrich_fn, p.author_url, op, summary,
                                            max_enrich - enrich_count)
        if enriched is None:
            continue
        enrich_count += 1
        if not is_us(enriched.get("country"), enriched.get("city")):
            _tally_skip(summary, "not_us")
            continue
        if not enriched.get("company"):
            _tally_skip(summary, "enrich_no_company")
            continue

        engager = Engager(
            full_name=enriched.get("full_name") or p.author_name,
            job_title=enriched.get("job_title") or p.author_headline,
            company_name=enriched.get("company"),
            company_website=enriched.get("company_domain"),
            industry=enriched.get("industry"),
            linkedin_url=enriched.get("linkedin_url") or p.author_url,
            source="event",
            engagement_type="comment",
            post_url=p.post_url,
            post_title=p.text,        # ingest re-checks attendance on this text
            comment_text=p.text,
            event_name=p.keyword or kw_label,
        )
        kw = {"repo": repo, "op": op, "can_qualify": can_qualify, "abm_lookup": abm_lookup}
        if qualify_fn is not None:
            kw["qualify_fn"] = qualify_fn
        _tally_result(summary, await ingest_engager(engager, **kw))

    logger.info("event poll: %s", summary)
    return summary


async def _enrich_and_record(enrich_fn, url: str, op, summary: dict, budget: int) -> dict | None:
    """One paid enrichment, the shared tail of both poll branches.

    Returns the enriched dict ({} if the profile didn't resolve) on a real call —
    and records the cost + bumps the `enriched` counter then; returns None to skip
    (the per-run cap is exhausted, or a transient Apify error — neither of which
    should burn a cap slot or count as enriched). The caller bumps enrich_count
    only on a non-None result.
    """
    if budget <= 0:
        _tally_skip(summary, "enrich_cap")
        return None
    try:
        enriched = await enrich_fn(url) or {}
    except apify.ApifyError:
        logger.exception("apify enrich failed for %s", url)
        _tally_skip(summary, "enrich_error")
        return None
    if op is not None:
        op.record(step="enrich", actual_usd=ENRICH_COST_USD,
                  company_key=None, model="apify:fresh-linkedin-profile")
    summary["enriched"] += 1
    return enriched
