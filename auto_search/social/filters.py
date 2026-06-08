"""Two pure gates applied before the (paid) qualifier: drop Magical's own staff,
and — for event signals — confirm the person is actually attending.

Both are deterministic and cheap by design: they run BEFORE the LLM ICP check so
we never spend a qualifier call on a colleague or a non-attendee.
"""

from __future__ import annotations

import re

from auto_search.normalize import normalize_company_name

# Magical's own identifiers — an engager at Magical is a colleague, not a lead.
_MAGICAL_KEY = normalize_company_name("Magical")
_MAGICAL_MARKERS = ("getmagical", "magical.com", "company/getmagical")


def is_magical(company_name: str | None, *links: str | None) -> bool:
    """True if the engager works at Magical (drop them).

    Matches the normalized company name exactly (so "Magical Inc" → True but
    "Magical Touch Dental" → False), or any Magical URL marker in the supplied
    links (LinkedIn company URL / website)."""
    if company_name and normalize_company_name(company_name) == _MAGICAL_KEY:
        return True
    for link in links:
        low = (link or "").lower()
        if low and any(m in low for m in _MAGICAL_MARKERS):
            return True
    return False


# Phrases that signal a person is (or will be) attending the event. Word-
# Attendance intent — present/future ("I'll be at"), PAST ("I attended", "great
# time at", "back from"), and exhibitor/speaker forms ("our booth", "exhibiting",
# "speaking/presenting at", "meet (me/us/the team) at"). Word-boundaried. For an
# event keyword search the post already mentions the event, so we additionally
# require an attendance VERB here to exclude pure topic commentary ("HIMSS26 will
# be huge for the industry").
_ATTENDING_RE = re.compile(
    r"\b(?:i'?ll be (?:there|attending|at)|see you (?:there|at)|join (?:me|us)(?: at| for)?"
    r"|excited to (?:attend|be at|join)|attending|attended|registered for|signed up"
    r"|count me in|i'?m in|stop(?:ping)? by|swing by|at booth|our booth|visit (?:us|me|our booth)"
    r"|find (?:me|us) at|meet (?:me|us|the team|our team) at|here at|back from|coming back from"
    r"|(?:great|fantastic|amazing|incredible|wonderful|awesome) (?:time|day|days|to be) (?:at|in)"
    r"|was (?:great|amazing)? ?at"
    r"|looking forward to (?:seeing|attending|being)|can'?t wait to (?:attend|see|be)"
    # past-event recaps + exhibitor/speaker/on-the-ground forms — an event post is
    # often the author reflecting on having been there.
    r"|(?:just |recently )?wrapped|is done|was (?:a )?(?:blast|success)|loved every"
    r"|takeaways from|reflections from|highlights from|(?:our|my) (?:time|takeaways) (?:at|from)"
    r"|presence at|exhibiting|presenting at|speaking at|we (?:are|were|'?re) at|i (?:was|am) at"
    r"|live (?:from|at)|reporting (?:live )?from|on the ground at)\b",
    re.IGNORECASE,
)

# Explicit declines — these FLIP the meaning, so they must win over the positive
# regex ("not attending" contains "attending"; "can't make it" looks eager).
_NOT_ATTENDING_RE = re.compile(
    r"\b(?:not (?:attending|going|coming|able to (?:attend|make|join)|be (?:there|attending))"
    r"|won't (?:be (?:there|attending|able)|make it)|can't make it|unable to attend"
    r"|won't make it|gonna miss|going to miss|miss(?:ing)? (?:it|this one))\b",
    re.IGNORECASE,
)


def _normalize_quotes(text: str | None) -> str:
    """Fold curly apostrophes to ASCII so "I’ll"/"can’t" (iOS/LinkedIn) match."""
    return (text or "").replace("’", "'").replace("ʼ", "'")


# US country/location markers (the enrichment returns e.g. country="United States",
# location_name="Phoenix, Arizona, United States").
_US_VALUES = frozenset({"us", "usa", "u.s.", "u.s.a.", "united states",
                        "united states of america", "america"})
_US_LOCATION_RE = re.compile(
    r"\b(?:united states|usa|u\.s\.a?\.?|, us\b)\b", re.IGNORECASE)


def is_us(country: str | None, *location_hints: str | None) -> bool:
    """True if the person is US-based (Magical sells into US healthcare only).

    Trusts an explicit country first; falls back to a US marker in any supplied
    location string. Unknown/empty → False (we'd rather skip than chase a non-US
    lead), so the caller drops anyone we can't confirm is in the US.
    """
    if country and country.strip().lower() in _US_VALUES:
        return True
    for hint in (country, *location_hints):
        if hint and _US_LOCATION_RE.search(hint):
            return True
    return False


def is_attending(comment_text: str | None, post_title: str | None = None) -> tuple[bool, str]:
    """Return (is_attending, matched_phrase).

    Event engagers are only worth pursuing if they're actually going. We can
    only confirm that from text — a comment expressing attendance intent, or a
    post the person authored saying so. A bare like with no text is NOT
    confirmation (returns False). An explicit decline ("not attending", "can't
    make it") returns False even though it contains attendance words.
    """
    texts = [t for t in (_normalize_quotes(comment_text), _normalize_quotes(post_title)) if t]
    if any(_NOT_ATTENDING_RE.search(t) for t in texts):
        return False, ""
    for text in texts:
        m = _ATTENDING_RE.search(text)
        if m:
            return True, m.group(0)
    return False, ""
