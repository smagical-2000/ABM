"""DTOs for ABM target-list matching."""

from __future__ import annotations

from pydantic import BaseModel


class TargetAccount(BaseModel):
    """One account from the uploaded ABM target list (one workbook row).

    `keys` holds the normalized dedup keys for the primary name AND any
    expanded (FKA ...)/(AKA ...) aliases, so a former name still matches.
    `domain`/`state` are populated only when the source sheet carried them.
    """

    name: str
    aliases: list[str] = []
    keys: list[str] = []
    domain: str | None = None
    state: str | None = None              # 2-letter US state, when present
    segment: str | None = None            # human label derived from the sheet
    source_sheet: str | None = None
    definitive_id: str | None = None


class AbmMatch(BaseModel):
    """Result of matching a discovered company to the target list.

    tier:
      confirmed - domain match, or name match where the discovered signal's
                  state agrees with the target (different orgs that share a
                  name in different states are NOT conflated)
      review    - name match only, with no state corroboration (lower trust)
    how: "domain" | "name+state" | "name"
    """

    tier: str
    how: str
    target_name: str
    source_sheet: str | None = None
    segment: str | None = None
    state: str | None = None
