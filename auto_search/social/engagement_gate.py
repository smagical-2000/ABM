"""Competitor-engagement noise gate.

A single like on a competitor's post is weak intent — it could be anything.
Repeated likes, or any comment, is a person genuinely paying attention to the
competitive space, which IS a buying signal. This module decides, per person,
whether their engagement with COMPETITOR posts has stacked enough to be worth
surfacing (and paying to enrich/qualify).

Magical's OWN posts are exempt — engaging with us directly is intent on its own,
so the caller never runs this gate for `magical_post`.

Pure + deterministic: a function of the passed engagers only. Same shape and
"wait for the stack before you spend" philosophy as `job_stacking`, applied to
social instead of hiring.
"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass

# Distinct like/reaction engagements (no comment) a person must have on competitor
# posts before they surface. 2 = "more than a one-off like". A comment always
# qualifies on its own. Env-tunable, floored at 2.
MIN_COMPETITOR_LIKES = max(2, int(os.getenv("SOCIAL_COMPETITOR_MIN_LIKES", "2")))


@dataclass(frozen=True)
class EngagementTally:
    """How one person engaged with the competitor posts pulled this run."""

    likes: int = 0          # reactions without a comment
    comments: int = 0       # comments — any one is strong intent on its own

    @property
    def qualifies(self) -> bool:
        """Surfaces if they commented at all, or liked at least MIN times."""
        return self.comments >= 1 or self.likes >= MIN_COMPETITOR_LIKES


def _ident(engager) -> str:
    return (getattr(engager, "linkedin_url", None) or "").strip().lower()


def _is_comment(engager) -> bool:
    return bool((getattr(engager, "comment_text", None) or "").strip())


def tally_by_person(engagers) -> dict[str, EngagementTally]:
    """Aggregate raw engagers (one row per like/comment) into a per-person tally,
    keyed by lowercased profile URL.

    Counted from the RAW engagers — before the caller's first-wins dedup — so a
    person who liked three different posts reads as three likes, not one. An
    engager with no profile URL can't be identified, so it's dropped here.
    """
    likes: dict[str, int] = defaultdict(int)
    comments: dict[str, int] = defaultdict(int)
    for e in engagers:
        ident = _ident(e)
        if not ident:
            continue
        if _is_comment(e):
            comments[ident] += 1
        else:
            likes[ident] += 1
    return {i: EngagementTally(likes=likes[i], comments=comments[i])
            for i in set(likes) | set(comments)}


def competitor_engager_qualifies(tally: EngagementTally | None) -> bool:
    """True when a competitor engager has stacked enough to surface. A missing
    tally (no captured engagement for this person) never qualifies."""
    return tally is not None and tally.qualifies
