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

Domain-contradiction veto (2026-07-27 merge audit): a NAME match is refused when
the contact's corporate email domain and the account's stored domain provably
disagree, unless the pair carries a verified-same verdict (site_verify). Name
patterns alone put 155 national-CHS contacts on a Wisconsin FQHC and the NY
Healthfirst insurer's book on Florida's Health First — never again.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

from auto_search.engagement.notify import company_key
from auto_search.normalize import (
    clean_domain,
    normalize_company_name,
    registrable_domain,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AccountMatch:
    account_id: str
    name: str
    tier: str                    # 'domain' | 'name'
    lists: tuple[str, ...]       # ('scored',) | ('abm',) | ('scored', 'abm')


class CrossIndex:
    """In-memory index over scored accounts + ABM targets. O(1) dict lookups.

    `same_pairs` holds site_verify pair keys with a high-confidence 'same'
    verdict — the only thing that may relax the domain-contradiction veto."""

    def __init__(self, scored: Iterable[dict], abm_targets: Iterable[dict],
                 same_pairs: set[str] | None = None) -> None:
        self.same_pairs = same_pairs or set()
        self.collisions: list[str] = []
        # (contact_domain, account_domain, company) per veto — the sync feeds
        # these to site_verify so a legitimate second-corporate-domain pair
        # (adventhealth.com staff mailing from @ah.org) self-resolves instead
        # of staying unresolved forever (review 2026-07-27).
        self.vetoed_pairs: set[tuple[str, str, str]] = set()
        self._s_domain: dict[str, dict] = {}
        self._s_key: dict[str, dict] = {}
        for a in scored:
            aid = a.get("account_id")
            if not aid:
                continue
            dom = _usable_domain(a.get("domain"))
            rec = {"account_id": aid, "name": a.get("name") or aid, "domain": dom}
            # Exact host FIRST (wins at lookup), registrable form as fallback —
            # sibling hosts on one parent domain must not steal each other.
            exact = _exact_domain(a.get("domain"))
            if exact:
                self._note(self._s_domain, exact, rec, "scored-domain")
            if dom and dom != exact:
                self._note(self._s_domain, dom, rec, "scored-domain")
            for key in _name_keys(a.get("name") or ""):
                self._note(self._s_key, key, rec, "scored-name")

        self._a_domain: dict[str, dict] = {}
        self._a_key: dict[str, dict] = {}
        # ABM target -> its SCORED sibling (same primary key or domain). When a
        # contact matches an abm target only via an alias/variant name (e.g.
        # company "Summa Health" vs scored name "Summa Health System"), crossing
        # must resolve to the SCORED id — the merged-row rule. Without this, every
        # daily re-sync re-minted an abm_ twin for an already-scored company and
        # split its heat across two board tiles (MAR2-32, re-found 2026-07-14).
        self._abm_sibling: dict[str, dict] = {}
        for t in abm_targets:
            name = t.get("name") or ""
            primary = normalize_company_name(name)
            if not primary:
                continue
            dom = _usable_domain(t.get("domain"))
            rec = {"account_id": f"abm_{primary}", "name": name, "domain": dom}
            exact = _exact_domain(t.get("domain"))
            if exact:
                self._note(self._a_domain, exact, rec, "abm-domain")
            if dom and dom != exact:
                self._note(self._a_domain, dom, rec, "abm-domain")
            # match on the target's normalized name + any expanded aliases,
            # plus the company_key form (dual-form, same as the scored side)
            alias_keys = {k for k in (t.get("keys") or [primary])
                          if _usable_name_key_raw(k)}
            alias_keys.update(_name_keys(name))
            for key in sorted(alias_keys):
                self._note(self._a_key, key, rec, "abm-alias")
            # Sibling link gate (2026-07-27): a name-keyed sibling with a
            # CONFLICTING domain is the Healthfirst-NY-onto-FL mechanism —
            # refuse it unless site_verify says the pair is the same company.
            sib = next((self._s_key[k] for k in _name_keys(name)
                        if k in self._s_key), None) \
                or (self._s_domain.get(dom) if dom else None)
            if sib and not _domains_compatible(dom, sib.get("domain"),
                                               self.same_pairs):
                logger.warning(
                    "cross: refusing abm sibling %s -> %s (domain conflict "
                    "%s vs %s)", rec["account_id"], sib["account_id"],
                    dom, sib.get("domain"))
                # Queue for verification like the match-time vetoes — the first
                # two live refusals (den.health/denverhealth.org,
                # scanhealthplan.com/thescangroup.org) are both plausibly the
                # SAME org, and without this they'd stay split forever.
                self.vetoed_pairs.add((dom or "", sib.get("domain") or "", name))
                sib = None
            if sib:
                self._abm_sibling[rec["account_id"]] = sib

    def _note(self, index: dict, key: str, rec: dict, kind: str) -> None:
        """setdefault + collision observability: a silently shadowed company
        never matches anything (655 live alias collisions, 2026-07-27 audit)."""
        prior = index.setdefault(key, rec)
        if prior is not rec and prior.get("account_id") != rec.get("account_id"):
            note = (f"{kind} key {key!r}: {rec['account_id']} shadowed by "
                    f"{prior['account_id']}")
            self.collisions.append(note)
            if not _domains_compatible(rec.get("domain"), prior.get("domain"),
                                       self.same_pairs):
                logger.warning("cross index collision (cross-domain): %s", note)

    @property
    def size(self) -> tuple[int, int]:
        return len(self._s_key), len(self._a_key)

    def match(self, *, company: str | None = None, domain: str | None = None,
              email: str | None = None) -> AccountMatch | None:
        raw_dom = domain if _exact_domain(domain) else (
            (email or "").rsplit("@", 1)[-1] if "@" in (email or "") else None)
        exact = _exact_domain(raw_dom)
        dom = _usable_domain(raw_dom)
        keys = _name_keys(company or "")
        scored, s_tier = _lookup(self._s_domain, self._s_key, exact, dom, keys)
        abm, a_tier = _lookup(self._a_domain, self._a_key, exact, dom, keys)
        # Domain-contradiction veto: a name-tier hit whose stored domain
        # conflicts with the contact's own corporate domain is refused — the
        # contact stays unresolved-for-review instead of merging wrongly.
        if scored and s_tier == "name" and not _domains_compatible(
                dom, scored.get("domain"), self.same_pairs):
            logger.info("cross: name-match veto %s (contact %s vs account %s)",
                        scored["account_id"], dom, scored.get("domain"))
            self.vetoed_pairs.add((dom, scored.get("domain") or "",
                                   scored.get("name") or ""))
            scored = None
        if abm and a_tier == "name" and not _domains_compatible(
                dom, abm.get("domain"), self.same_pairs):
            logger.info("cross: name-match veto %s (contact %s vs target %s)",
                        abm["account_id"], dom, abm.get("domain"))
            self.vetoed_pairs.add((dom, abm.get("domain") or "",
                                   abm.get("name") or ""))
            abm = None
        if scored:
            lists = ("scored", "abm") if abm else ("scored",)
            return AccountMatch(scored["account_id"], scored["name"], s_tier, lists)
        if abm:
            # Alias/variant hit on an abm target whose company IS scored: return
            # the scored sibling (merged-row rule) so re-syncs can never re-mint
            # an abm_ twin for a scored company.
            sib = self._abm_sibling.get(abm["account_id"])
            # Sibling-hop veto (review 2026-07-27, reproduced): when the ABM
            # target row has NO domain, the build-time gate can't see a
            # conflict — so a contact vetoed against the scored account would
            # re-reach it through the hop. Re-check against the CONTACT here;
            # on conflict the contact stays on the abm_ tile (never merged —
            # the heal manual-queues conflicting-domain twins).
            if sib and not _domains_compatible(dom, sib.get("domain"),
                                               self.same_pairs):
                logger.info("cross: sibling-hop veto %s -/-> %s (contact %s "
                            "vs sibling %s)", abm["account_id"],
                            sib["account_id"], dom, sib.get("domain"))
                self.vetoed_pairs.add((dom, sib.get("domain") or "",
                                       sib.get("name") or ""))
                sib = None
            if sib:
                return AccountMatch(sib["account_id"], sib["name"], a_tier,
                                    ("scored", "abm"))
            return AccountMatch(abm["account_id"], abm["name"], a_tier, ("abm",))
        return None


def build_index(scoring_repo, discovery_repo, engagement_repo=None) -> CrossIndex:
    """Build the index from the live repos (scored accounts + ABM targets).
    With an engagement repo, verified-same domain pairs (site_verify) preload
    so the domain-contradiction veto can spare known-same pairs (kp.org)."""
    scored = scoring_repo.list_accounts() if hasattr(scoring_repo, "list_accounts") else []
    abm = discovery_repo.abm_targets() if hasattr(discovery_repo, "abm_targets") else []
    same: set[str] = set()
    if engagement_repo is not None:
        try:
            from auto_search.engagement import site_verify
            same = site_verify.verified_same_pairs(engagement_repo)
        except Exception:  # noqa: BLE001 — the veto just runs stricter without it
            logger.warning("cross: verdict cache unavailable, veto unrelaxed")
    return CrossIndex(scored, abm, same_pairs=same)


# ── helpers ────────────────────────────────────────────────────────────


def _lookup(by_domain: dict, by_key: dict, exact: str | None, dom: str | None,
            keys: list[str]):
    # Exact host outranks the registrable-collapsed fallback (sibling hosts
    # on one parent domain must resolve to their own account).
    if exact and exact in by_domain:
        return by_domain[exact], "domain"
    if dom and dom in by_domain:
        return by_domain[dom], "domain"
    for key in keys:
        if key in by_key:
            return by_key[key], "name"
    return None, None


def _domains_compatible(a: str | None, b: str | None,
                        same_pairs: set[str]) -> bool:
    """True unless BOTH sides carry a domain and they resolve to different
    registrable domains without a verified-same verdict. A missing domain is
    not evidence of anything — those pairs pass (and stay heal-guarded)."""
    ra, rb = registrable_domain(a), registrable_domain(b)
    if not ra or not rb or ra == rb:
        return True
    return "|".join(sorted((ra, rb))) in same_pairs


# Free / personal / relay email providers — never a usable account-matching
# domain. Skip them so a personal-email contact can't domain-match (it falls
# through to company name), and a stray personal domain on a scored/ABM row
# never becomes an index key.
_PERSONAL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "yahoo.com", "ymail.com", "hotmail.com",
    "outlook.com", "live.com", "msn.com", "aol.com", "icloud.com", "me.com",
    "mac.com", "proton.me", "protonmail.com", "comcast.net", "att.net", "verizon.net",
    "privaterelay.appleid.com", "duck.com", "pm.me", "mail.com",
    "unknown.com", "example.com", "noemail.com",
})


def _usable_domain(value: str | None) -> str | None:
    """Registrable-collapsed matching domain (email.chop.edu == chop.edu —
    subdomains were defeating domain matches and dropping contacts to the
    unguarded name tier). Stored values stay verbatim; only keys collapse."""
    dom = registrable_domain(clean_domain(value))
    return dom if dom and dom not in _PERSONAL_DOMAINS else None


def _exact_domain(value: str | None) -> str | None:
    """Uncollapsed host for domain-index precedence: two subsidiaries on one
    parent registrable domain (mercy.trinityhealth.org / stjoes.…) each keep
    their exact-host match; the registrable form is only the fallback key
    (review 2026-07-27 — the collapse must never OUTRANK an exact host)."""
    dom = clean_domain(value)
    return dom if dom and dom not in _PERSONAL_DOMAINS else None


# Normalized names that collapse to a single generic industry/specialty word are
# too broad to be a reliable NAME-match key — "Radiology Partners", "Radiology
# Inc" and any junk lead whose company is just "Radiology" would all collide on
# "radiology" (2026-07-27 audit: 22 Radiology Partners contacts on a small
# Indiana practice). Such accounts still match on domain and on their
# suffix-carrying company_key form; they just don't seed the collapsed key.
# Distinctive single words (healogics, caresource, athletico) stay matchable.
_GENERIC_NAME_KEYS = frozenset({
    "medical", "health", "healthcare", "clinic", "clinics", "care", "group",
    "hospital", "hospitals", "center", "centers", "dental", "wellness", "family",
    "medicine", "physicians", "associates", "partners", "services",
    # medical-specialty vocabulary (2026-07-27 audit — the stoplist gap that
    # let "radiology" through)
    "radiology", "surgery", "surgical", "rehab", "rehabilitation", "allergy",
    "anesthesia", "gi", "urology", "cardiology", "oncology", "orthopedic",
    "orthopedics", "orthopaedic", "orthopaedics", "pediatric", "pediatrics",
    "dermatology", "neurology", "imaging", "therapy", "behavioral",
    "psychiatry", "psychology", "primary", "kidney", "spine", "pain",
    "women", "children", "community", "regional", "banner",
})


def _name_keys(name: str | None) -> list[str]:
    """Every usable name key for a raw company name, strongest first: the
    normalized (suffix-stripped) form unless degenerate, plus the company_key
    form when it differs (keeps "The Valley Hospital" == "Valley Hospital" and
    lets suffix-variants of degenerate-class names still match exactly).
    Index and query both use this list, so the two sides can never skew."""
    keys: list[str] = []
    norm = normalize_company_name(name or "")
    if norm and norm not in _GENERIC_NAME_KEYS:
        keys.append(norm)
    ck = company_key(name or "")
    if ck and ck not in _GENERIC_NAME_KEYS and ck not in keys:
        keys.append(ck)
    return keys


def _usable_name_key_raw(key: str | None) -> str | None:
    """Guard for PRE-NORMALIZED keys (abm alias lists store them normalized)."""
    return key if key and key not in _GENERIC_NAME_KEYS else None


def _email_domain(email: str | None) -> str | None:
    e = (email or "").strip().lower()
    return _usable_domain(e.rsplit("@", 1)[-1]) if "@" in e else None
