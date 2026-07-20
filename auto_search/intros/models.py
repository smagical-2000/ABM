"""DTOs for warm-intro matching - founder profiles, contacts, and paths."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Stint(BaseModel):
    """One employment or education entry, reduced to what matching needs.

    `norm` is the normalized org key (company via normalize_company_name,
    school via a punctuation-collapse) - equality on `norm` is the match.
    `end_year` is 9999 while ongoing so range overlap stays simple math.
    """

    org: str                       # display name as scraped
    norm: str                      # normalized match key
    title: str | None = None
    start_year: int | None = None
    end_year: int | None = None    # 9999 = present


class FounderProfile(BaseModel):
    name: str
    linkedin_url: str
    headline: str | None = None
    experiences: list[Stint] = Field(default_factory=list)
    educations: list[Stint] = Field(default_factory=list)
    scraped_at: str | None = None


class WarmPath(BaseModel):
    """One reason a contact is warm. `strength` sorts (higher = warmer)."""

    kind: str                      # engaged | shared_employer | shared_school
    founder: str | None = None     # None for 'engaged' (it's about Magical, not a person)
    evidence: str                  # human-readable, e.g. "Both at Olive - overlap 2019-2021"
    strength: int


class WarmContact(BaseModel):
    """A decision-maker at the account, with any warm paths attached."""

    name: str
    title: str | None = None       # current title / headline
    linkedin_url: str | None = None
    location: str | None = None
    schools: list[str] = Field(default_factory=list)   # alma maters, on file for reps
    paths: list[WarmPath] = Field(default_factory=list)

    @property
    def warmth(self) -> int:
        return max((p.strength for p in self.paths), default=0)
