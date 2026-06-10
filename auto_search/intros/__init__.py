"""Warm-intro paths — who at a target account can we reach, and how warmly?

For a SCORED account, "Find warm intros" answers two questions with evidence:

  1. WHO are the ICP decision-makers there (the user's title list: C-suite /
     VP / Director of revenue cycle, finance, patient access, IT, digital...)?
     Found via a LinkedIn people search scoped to the account + titles.
  2. HOW WARM is each one, relative to Magical's founders?
       - engaged          they liked/commented on Magical's posts (we already
                          capture this in social listening) - hottest
       - shared_employer  founder and contact worked at the same company,
                          ideally with overlapping years
       - shared_school    same school (overlapping years rank higher)
     A contact with no path still shows - the right title at the right account
     is the product; warmth is the ranking.

Every path cites its evidence (company + years, the school, the engaged post).
Nothing is inferred by an LLM - paths are deterministic profile-data overlaps,
so a path shown is a path that exists.
"""

from auto_search.intros import paths, profiles, service

__all__ = ["paths", "profiles", "service"]
