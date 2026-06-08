"""Normalized social-engagement payload + the RawSignal it becomes.

`Engager` is the shape our webhook accepts from Trigify (one person who liked or
commented, already enriched by the Trigify workflow). `to_signal()` turns it into
the source-agnostic `RawSignal` the rest of the discovery pipeline already
understands — so a social engager flows through the same qualifier, dedup, and
panel as a layoff or a funding round, just with `signal_type='social_engagement'`
(or `'event_attendance'`) and the person carried in the payload.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from auto_search.models import RawSignal

# Whose content was engaged with — sets the intent weight. Magical's own post is
# the hottest signal; a competitor's is warm; an event/keyword is topical.
SocialSource = Literal["magical_post", "competitor_post", "event"]

_SOURCE_STRENGTH: dict[str, float] = {
    "magical_post": 0.9,
    "competitor_post": 0.7,
    "event": 0.6,
}

# A monitored LinkedIn account whose post engagers we scrape. `kind` sets the
# intent weight of everyone who engages with it: our own posts are the hottest
# signal, a competitor's are warm.
TargetKind = Literal["own", "competitor"]


def source_for_kind(kind: str) -> SocialSource:
    """Map a monitored-account kind to the engagement source/intent weight."""
    return "magical_post" if kind == "own" else "competitor_post"


class SocialTarget(BaseModel):
    """A LinkedIn profile/company we monitor for post engagement."""

    linkedin_url: str
    label: str | None = None
    kind: TargetKind = "competitor"
    active: bool = True


class Engager(BaseModel):
    """One enriched person who engaged with a tracked post / event."""

    # person (from Trigify person_enrichment)
    full_name: str
    job_title: str | None = None
    job_title_levels: list[str] = Field(default_factory=list)
    job_title_role: str | None = None
    company_name: str | None = None
    company_website: str | None = None     # often a LinkedIn URL, not a real domain
    industry: str | None = None
    linkedin_url: str | None = None
    # engagement context
    source: SocialSource
    engagement_type: Literal["like", "comment"] = "like"
    reaction_type: str | None = None       # like | celebrate | support | …
    post_url: str | None = None
    post_title: str | None = None          # post text/headline, for context + attendance
    comment_text: str | None = None
    event_name: str | None = None
    engaged_at: datetime | None = None

    @field_validator("job_title_levels", mode="before")
    @classmethod
    def _coerce_levels(cls, v: object) -> list[str]:
        """Trigify sends job_title_levels inconsistently (list, comma string, or
        absent). Normalize to a list so a string never bounces the whole record."""
        if v is None:
            return []
        if isinstance(v, str):
            return [p.strip() for p in v.split(",") if p.strip()]
        if isinstance(v, (list, tuple)):
            return [str(x).strip() for x in v if str(x).strip()]
        return [str(v).strip()]

    def to_signal(self) -> RawSignal:
        """Build the RawSignal for this engager (company-keyed, person in payload)."""
        signal_type = "event_attendance" if self.source == "event" else "social_engagement"
        # Stable per (person, engagement_type, post/event) so re-delivery of the
        # same like dedups, but a person's like AND comment on one post are kept
        # as distinct signals. Falls back to name when the profile URL is absent.
        identity = (self.linkedin_url or self.full_name or "").strip().lower()
        context = (self.post_url or self.event_name or "").strip().lower()
        external_id = f"{self.source}:{self.engagement_type}:{identity}:{context}"
        payload = {
            "person_name": self.full_name,
            "person_title": self.job_title,
            "person_profile_url": self.linkedin_url,
            "person_company": self.company_name,
            "engagement_type": self.engagement_type,
            "reaction_type": self.reaction_type,
            "post_url": self.post_url,
            "post_title": self.post_title,
            "comment_text": self.comment_text,
            "event_name": self.event_name,
            "social_source": self.source,
        }
        return RawSignal(
            source=f"social_{self.source}",
            source_external_id=external_id,
            signal_type=signal_type,
            company_name_raw=self.company_name or "",
            signal_strength=_SOURCE_STRENGTH.get(self.source, 0.6),
            payload=payload,
            observed_at=self.engaged_at or datetime.now(UTC),
        )


class IngestResult(BaseModel):
    """Outcome of ingesting one engager — for the webhook's per-record report."""

    accepted: bool
    action: str          # qualified | appended | duplicate | skipped
    reason: str          # why (e.g. not_decision_maker, attendance_unconfirmed, qualified)
    company_key: str | None = None
    company_name: str | None = None
