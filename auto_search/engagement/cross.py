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
            dom = _usable_domain(a.get("domain"))
            if dom:
                self._s_domain.setdefault(dom, rec)
            key = _usable_name_key(normalize_company_name(a.get("name") or ""))
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
            dom = _usable_domain(t.get("domain"))
            if dom:
                self._a_domain.setdefault(dom, rec)
            # match on the target's normalized name + any expanded aliases
            for key in (t.get("keys") or [primary]):
                if _usable_name_key(key):
                    self._a_key.setdefault(key, rec)

    @property
    def size(self) -> tuple[int, int]:
        return len(self._s_key), len(self._a_key)

    def match(self, *, company: str | None = None, domain: str | None = None,
              email: str | None = None) -> AccountMatch | None:
        dom = _usable_domain(domain) or _email_domain(email)
        key = _usable_name_key(normalize_company_name(company or ""))
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


# Free / personal email providers — never a usable account-matching domain. Skip
# them so a personal-email contact can't domain-match (it falls through to company
# name), and a stray personal domain on a scored/ABM row never becomes an index key.
_PERSONAL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "hotmail.com",
    "outlook.com", "live.com", "msn.com", "aol.com", "icloud.com", "me.com",
    "mac.com", "proton.me", "protonmail.com", "comcast.net", "att.net", "verizon.net",
})


def _usable_domain(value: str | None) -> str | None:
    dom = clean_domain(value)
    return dom if dom and dom not in _PERSONAL_DOMAINS else None


# Normalized names that collapse to a single generic industry word are too broad to
# be a reliable NAME-match key — e.g. the ABM target "Medical Associates" normalizes
# to "medical", which then catches any junk lead whose company is just "Medical".
# Such accounts can still match on domain (more reliable); they just don't seed a
# name key. Keep this list tight so it only blocks genuinely degenerate keys.
_GENERIC_NAME_KEYS = frozenset({
    "medical", "health", "healthcare", "clinic", "clinics", "care", "group",
    "hospital", "hospitals", "center", "centers", "dental", "wellness", "family",
    "medicine", "physicians", "associates", "partners", "services",
})


def _usable_name_key(key: str | None) -> str | None:
    """A normalized company name usable for name-matching: non-empty, not a single
    generic industry word. (Domain matching is unaffected.)"""
    return key if key and key not in _GENERIC_NAME_KEYS else None


def _email_domain(email: str | None) -> str | None:
    e = (email or "").strip().lower()
    return _usable_domain(e.rsplit("@", 1)[-1]) if "@" in e else None
