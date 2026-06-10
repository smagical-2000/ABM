"""Pure warm-path matching - founder x contact profile overlaps. No I/O, no LLM.

Strength ladder (sorting, higher = warmer):
    100  engaged             they interacted with Magical's own posts
     80  shared_employer     same company, overlapping years (they may know each other)
     60  shared_employer     same company, different eras (shared tribe)
     50  shared_school       same school, overlapping years
     40  shared_school       same school, different eras

Companies normalize via the platform's canonical normalize_company_name (one
dedup brain everywhere); schools via a simple punctuation collapse - schools
are compared by full-string equality, deliberately conservative ("University of
Michigan" must never match "Michigan State").
"""

from __future__ import annotations

import re

from auto_search.intros.models import FounderProfile, Stint, WarmContact, WarmPath
from auto_search.normalize import normalize_company_name

_WS = re.compile(r"[^a-z0-9]+")

STRENGTH = {
    "engaged": 100,
    "shared_employer_overlap": 80,
    "shared_employer": 60,
    "shared_school_overlap": 50,
    "shared_school": 40,
}


def norm_school(name: str | None) -> str:
    """Conservative school key: lowercase, punctuation collapsed, full string."""
    return _WS.sub(" ", (name or "").lower()).strip()


def norm_company(name: str | None) -> str:
    return normalize_company_name(name or "")


def year_overlap(a: Stint, b: Stint) -> tuple[int, int] | None:
    """Overlapping [start, end] years of two stints, or None.

    Missing start years make overlap unknowable -> None (callers then report
    the weaker 'different eras / dates unknown' form rather than guessing).
    """
    if not a.start_year or not b.start_year:
        return None
    start = max(a.start_year, b.start_year)
    end = min(a.end_year or 9999, b.end_year or 9999)
    return (start, end) if start <= end else None


def _span(o: tuple[int, int]) -> str:
    return f"{o[0]}-{'now' if o[1] >= 9999 else o[1]}"


def founder_paths(founder: FounderProfile, contact: WarmContact,
                  contact_exp: list[Stint], contact_edu: list[Stint]) -> list[WarmPath]:
    """All employer/school overlap paths between one founder and one contact."""
    out: list[WarmPath] = []
    seen: set[tuple[str, str]] = set()      # (kind, org-norm) - one path per org

    for fe in founder.experiences:
        for ce in contact_exp:
            if not fe.norm or fe.norm != ce.norm or ("emp", fe.norm) in seen:
                continue
            seen.add(("emp", fe.norm))
            o = year_overlap(fe, ce)
            if o:
                out.append(WarmPath(
                    kind="shared_employer", founder=founder.name,
                    evidence=f"Both at {fe.org} - overlapping {_span(o)}",
                    strength=STRENGTH["shared_employer_overlap"]))
            else:
                out.append(WarmPath(
                    kind="shared_employer", founder=founder.name,
                    evidence=f"Both worked at {fe.org} (different periods)",
                    strength=STRENGTH["shared_employer"]))

    for fs in founder.educations:
        for cs in contact_edu:
            if not fs.norm or fs.norm != cs.norm or ("sch", fs.norm) in seen:
                continue
            seen.add(("sch", fs.norm))
            o = year_overlap(fs, cs)
            if o:
                out.append(WarmPath(
                    kind="shared_school", founder=founder.name,
                    evidence=f"Both at {fs.org} - overlapping {_span(o)}",
                    strength=STRENGTH["shared_school_overlap"]))
            else:
                out.append(WarmPath(
                    kind="shared_school", founder=founder.name,
                    evidence=f"Both attended {fs.org}",
                    strength=STRENGTH["shared_school"]))
    return out


def engaged_path(contact: WarmContact, engaged_urls: set[str],
                 engaged_names: set[str]) -> WarmPath | None:
    """The hottest path: this person already engaged with Magical's posts.

    Matched by profile-URL slug when we have it (safe), else by normalized
    full name (two same-named people could collide, so URL wins).
    """
    slug = _profile_slug(contact.linkedin_url)
    if slug and slug in engaged_urls:
        return WarmPath(kind="engaged", founder=None,
                        evidence="Engaged with Magical's LinkedIn posts",
                        strength=STRENGTH["engaged"])
    name = _WS.sub(" ", (contact.name or "").lower()).strip()
    if name and name in engaged_names:
        return WarmPath(kind="engaged", founder=None,
                        evidence="Engaged with Magical's LinkedIn posts (name match)",
                        strength=STRENGTH["engaged"])
    return None


def _profile_slug(url: str | None) -> str:
    u = (url or "").lower().split("?")[0].rstrip("/")
    return u.rsplit("/", 1)[-1] if "/in/" in u else ""


def rank(contacts: list[WarmContact]) -> list[WarmContact]:
    """Warmest first; ties keep search order (search relevance)."""
    return sorted(contacts, key=lambda c: -c.warmth)
