"""Match a discovered company against the ABM target list - pure and zero-cost.

Build the index once from the parsed target list, then call `match()` per
discovered company: O(1) dict lookups, no LLM, no network.

Precision model (strict by default):
  - a domain match is always 'confirmed' - a domain belongs to one org;
  - a name match is 'confirmed' only when the discovered company's signal
    location agrees on US state, so "Parkview Health (IN)" found in discovery
    is never conflated with a differently-located "Parkview Health System
    (CO)" on the list;
  - a name match with no state corroboration is returned as 'review' (still
    surfaced, but clearly lower-trust) rather than silently dropped.
"""

from __future__ import annotations

from auto_search.abm.models import AbmMatch, TargetAccount
from auto_search.abm.util import bare_domain
from auto_search.normalize import normalize_company_name


class AbmIndex:
    """In-memory index over the target list: name-key -> targets, domain -> targets."""

    def __init__(self, targets: list[TargetAccount]) -> None:
        self._targets = list(targets)
        self._by_key: dict[str, list[TargetAccount]] = {}
        self._by_domain: dict[str, list[TargetAccount]] = {}
        for t in self._targets:
            for key in t.keys:
                self._by_key.setdefault(key, []).append(t)
            if t.domain:
                self._by_domain.setdefault(t.domain, []).append(t)

    @property
    def size(self) -> int:
        return len(self._targets)

    def match(
        self,
        name: str | None,
        *,
        domain: str | None = None,
        states: list[str] | None = None,
    ) -> AbmMatch | None:
        """Return the best match for a discovered company, or None.

        `states` are the US state codes seen on the company's signals (job
        locations); used only to corroborate a name match.
        """
        # 1) Domain is the strongest, location-independent signal.
        dom = bare_domain(domain)
        if dom and dom in self._by_domain:
            return _to_match(self._by_domain[dom][0], "confirmed", "domain")

        # 2) Normalized-name match, corroborated by state where possible.
        key = normalize_company_name(name or "")
        candidates = self._by_key.get(key)
        if not candidates:
            return None

        seen = {s.upper() for s in (states or []) if s}
        if seen:
            for target in candidates:
                if target.state and target.state.upper() in seen:
                    return _to_match(target, "confirmed", "name+state")
        return _to_match(candidates[0], "review", "name")


def _to_match(target: TargetAccount, tier: str, how: str) -> AbmMatch:
    return AbmMatch(
        tier=tier,
        how=how,
        target_name=target.name,
        source_sheet=target.source_sheet,
        segment=target.segment,
        state=target.state,
    )
