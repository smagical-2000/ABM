"""Apply ABM target-list matches to outbound records — the one seam the API uses.

Both the discovery panel and the scored board need the same question answered:
"is this company on the uploaded ABM target list?" Routing that through a single
function means both surfaces share one index and one precision model, and keeps
the API layer thin — it just supplies name/domain/signal-locations.

The two surfaces don't always have the same evidence: the panel can corroborate
a name match with the signal's US state, the scored board can't (scored accounts
don't keep signal geography). So a panel hit confirmed via name+state may read as
'review' on the scored board — but a match is never silently lost, only ever
shown at equal-or-lower confidence.

Pure and zero-cost: a no-op when no list is loaded, an O(1) index lookup when one
is. See `matcher.py` for the precision model (domain → confirmed; name+state →
confirmed; name-only → review).
"""

from __future__ import annotations

from collections.abc import Iterable

from auto_search.abm.matcher import AbmIndex
from auto_search.abm.models import AbmMatch
from auto_search.abm.util import extract_state


def states_from_locations(locations: Iterable[object]) -> list[str]:
    """US state codes parsed from signal location strings, for name corroboration.

    Non-location values and unparseable strings drop out, so callers can pass raw
    signal locations (some null, some "Lincoln, NE") without pre-filtering.
    """
    return [s for loc in locations if (s := extract_state(loc))]


def match_one(
    index: AbmIndex | None,
    *,
    name: str | None,
    domain: str | None = None,
    states: list[str] | None = None,
) -> AbmMatch | None:
    """Best ABM match for one company, or None when there's no list or no hit.

    `states` corroborate a name match (so two same-named orgs in different states
    aren't conflated); omit them and a name-only hit lands in the 'review' tier.
    """
    if index is None or not index.size:
        return None
    return index.match(name, domain=domain, states=states or [])
