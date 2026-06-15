"""Cross an engagement contact to an account we already track — PURE, zero-cost.

Mirrors auto_search/abm/matcher.py: build the index once from the scored accounts +
the ABM target list, then match per contact:

  1. email domain  -> scored account domain        (strongest)
  2. else company  -> normalize_company_name        -> scored account name
  3. else domain / company name                     -> ABM target

A company on BOTH lists is ONE account (the scored id), tagged ["scored","abm"] —
the merged-row rule. An ABM-only match gets a stable synthetic id "abm_<key>" that
self-heals to the scored id once that company is scored (re-crossed every sync). No
match -> None: the contact stays unresolved (surfaced for review, never guessed).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from auto_search.normalize import clean_domain, normalize_company_name


@dataclass(frozen=True)
class AccountMatch:
    account_id: str
    name: str
    tier: str                    # 'domain' | 'name'
    lists: tuple[str, ...]       # ('scored',) | ('abm',) | ('scored', 'abm')


class CrossIndex:
    """In-memory index over scored accounts + ABM targets. O(1) dict lookups."""

    def __init__(self, scored: Iterable[dict], abm_targets: Iterable[dict]) -> None:
        self._s_domain: dict[str, dict] = {}
        self._s_key: dict[str, dict] = {}
        for a in scored:
            aid = a.get("account_id")
            if not aid:
                continue
            rec = {"account_id": aid, "name": a.get("name") or aid}
            dom = clean_domain(a.get("domain"))
            if dom:
                self._s_domain.setdefault(dom, rec)
            key = normalize_company_name(a.get("name") or "")
            if key:
                self._s_key.setdefault(key, rec)

        self._a_domain: dict[str, dict] = {}
        self._a_key: dict[str, dict] = {}
        for t in abm_targets:
            name = t.get("name") or ""
            primary = normalize_company_name(name)
            if not primary:
                continue
            rec = {"account_id": f"abm_{primary}", "name": name}
            dom = clean_domain(t.get("domain"))
            if dom:
                self._a_domain.setdefault(dom, rec)
            # match on the target's normalized name + any expanded aliases
            for key in (t.get("keys") or [primary]):
                if key:
                    self._a_key.setdefault(key, rec)

    @property
    def size(self) -> tuple[int, int]:
        return len(self._s_key), len(self._a_key)

    def match(self, *, company: str | None = None, domain: str | None = None,
              email: str | None = None) -> AccountMatch | None:
        dom = clean_domain(domain) or _email_domain(email)
        key = normalize_company_name(company or "")
        scored, s_tier = _lookup(self._s_domain, self._s_key, dom, key)
        abm, a_tier = _lookup(self._a_domain, self._a_key, dom, key)
        if scored:
            lists = ("scored", "abm") if abm else ("scored",)
            return AccountMatch(scored["account_id"], scored["name"], s_tier, lists)
        if abm:
            return AccountMatch(abm["account_id"], abm["name"], a_tier, ("abm",))
        return None


def build_index(scoring_repo, discovery_repo) -> CrossIndex:
    """Build the index from the live repos (scored accounts + ABM targets)."""
    scored = scoring_repo.list_accounts() if hasattr(scoring_repo, "list_accounts") else []
    abm = discovery_repo.abm_targets() if hasattr(discovery_repo, "abm_targets") else []
    return CrossIndex(scored, abm)


# ── helpers ────────────────────────────────────────────────────────────


def _lookup(by_domain: dict, by_key: dict, dom: str | None, key: str | None):
    if dom and dom in by_domain:
        return by_domain[dom], "domain"
    if key and key in by_key:
        return by_key[key], "name"
    return None, None


def _email_domain(email: str | None) -> str | None:
    e = (email or "").strip().lower()
    return clean_domain(e.rsplit("@", 1)[-1]) if "@" in e else None
