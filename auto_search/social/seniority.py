"""Decision-maker seniority classifier for social engagers — pure, no LLM.

Magical's LinkedIn likes are full of its own staff and junior titles from other
orgs; only decision-makers are worth qualifying. The product bar is **Director &
above**: keep C-level, VP/SVP/EVP, Director, Head of, Owner/Founder/President,
Partner/Principal; drop Manager and below.

We classify from the free-text ``job_title`` because that's the reliable field —
Trigify's structured ``job_title_levels`` is frequently empty (verified on live
data). When ``job_title_levels`` IS populated we trust it as a confirming signal.
Deterministic and unit-tested so the bar can't drift silently.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# "Hard" executive tokens — decision-makers regardless of qualifier words.
# \bc[a-z]o\b covers CEO/CFO/COO/CTO/CIO/CMO/CRO/CNO/…; "chief" the spelled forms.
_HARD_EXEC_RE = re.compile(
    r"\bchief\b|\bc[a-z]o\b|\bcxo\b|\bowner\b|\bco[\s-]?founder\b|\bfounder\b"
    r"|\bmanaging\s+director\b",
    re.IGNORECASE,
)
# Plain President (NOT "Vice President", which is the VP tier and demotable).
_PRESIDENT_RE = re.compile(r"(?<!vice )(?<!vice-)\bpresident\b", re.IGNORECASE)
# VP tier — also Partner/Principal. Decision-makers UNLESS demoted (AVP, Associate
# Partner) or, for Principal, an individual-contributor discipline follows.
_VP_RE = re.compile(r"\b[se]?vp\b|\bvice[\s-]?president\b", re.IGNORECASE)
_PARTNER_RE = re.compile(r"\bpartner\b", re.IGNORECASE)
_PRINCIPAL_RE = re.compile(r"\bprincipal\b", re.IGNORECASE)
# "Principal Software Engineer", "Principal Scientist", "Principal Investigator"
# are senior INDIVIDUAL CONTRIBUTORS, not decision-makers.
_PRINCIPAL_IC_RE = re.compile(
    r"\bprincipal\b.*\b(engineer|scientist|investigator|consultant|architect"
    r"|analyst|developer|researcher|designer|specialist)\b",
    re.IGNORECASE,
)
# Director tier — a decision-maker UNLESS demoted by a junior qualifier.
_DIRECTOR_RE = re.compile(r"\bdirector\b|\bhead\s+of\b|\bglobal\s+head\b", re.IGNORECASE)

# Junior qualifiers that drop a would-be VP/Partner/Director/Head below the bar
# ("Assistant Vice President", "Associate Director", "Deputy Head").
_DEMOTE_RE = re.compile(
    r"\b(assistant|associate|deputy|junior|jr|trainee|intern|apprentice)\b",
    re.IGNORECASE,
)

# Structured job_title_levels values (when Trigify provides them) that are
# decision-maker tier. Normalized to lowercase + underscores before compare.
_DM_LEVELS = frozenset({
    "owner", "founder", "co_founder", "c_suite", "cxo", "chief", "vp",
    "vice_president", "svp", "evp", "director", "head", "partner",
    "president", "managing_director", "executive",
})


def _norm_levels(levels: Iterable[object] | None) -> set[str]:
    return {re.sub(r"[\s-]+", "_", str(v).strip().lower()) for v in (levels or []) if v}


def is_decision_maker(
    job_title: str | None,
    job_title_levels: Iterable[object] | None = None,
    job_title_role: str | None = None,  # accepted for symmetry; not load-bearing
) -> tuple[bool, str]:
    """Return (is_decision_maker, reason).

    reason ∈ {"level", "title_exec", "title_director", "below_bar", "no_title"}.
    Director & above qualifies; Manager and below does not. Guards the common
    false positives: AVP / Associate VP, Associate Partner, and IC "Principal
    Engineer / Scientist / Investigator" titles.
    """
    if _norm_levels(job_title_levels) & _DM_LEVELS:
        return True, "level"

    title = (job_title or "").strip()
    if not title:
        return False, "no_title"

    if _HARD_EXEC_RE.search(title):
        return True, "title_exec"

    demoted = bool(_DEMOTE_RE.search(title))
    if _PRESIDENT_RE.search(title) and not demoted:
        return True, "title_exec"
    if _VP_RE.search(title) and not demoted:
        return True, "title_exec"
    if _PARTNER_RE.search(title) and not demoted:
        return True, "title_exec"
    if _PRINCIPAL_RE.search(title) and not demoted and not _PRINCIPAL_IC_RE.search(title):
        return True, "title_exec"
    if _DIRECTOR_RE.search(title) and not demoted:
        return True, "title_director"
    return False, "below_bar"
