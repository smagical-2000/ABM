"""Enrollment eligibility + contact planning — PURE, deterministic, no I/O.

Mirrors priority.py / engagement/scoring.py: the single place the enrollment
rule lives, so it stays auditable and testable. The runner feeds it rows; it
answers WHO qualifies and WHICH contacts get sent, with a human reason per
account (a rank is never a black box — same rule as intent).

The locked rule (docs/CAMPAIGN_AUTOMATION_ARCHITECTURE.md §6, defaults from §9):

    eligible = scored            (state == 'scored', fit band High/Medium)
             AND in-market       (engagement heat >= Warm  OR  carried intent Hot)
             AND sendable        (>=1 matched, not-opted-out contact with an email)

Every gate appends a reason, so the Campaigns tab can show Galyna exactly why
an account is (or is not) in the ready-to-enroll list.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from auto_search import priority
from auto_search.campaigns import catalog
from auto_search.engagement import scoring as engagement_scoring

# Fit bands that qualify (scored_accounts.tier_band). "low"/"out" never enroll.
DEFAULT_FIT_BANDS: tuple[str, ...] = ("high", "medium")
# Heat tiers that count as in-market (engagement/scoring.py tiers).
DEFAULT_HEAT_TIERS: tuple[str, ...] = ("Warm", "Hot")


@dataclass
class Eligible:
    """One account that qualifies for enrollment, with the audit trail."""

    account_id: str
    name: str
    segment: str | None
    sequence_key: str
    fit_band: str
    fit_label: str | None
    heat_score: int
    heat_tier: str
    intent_tier: str | None
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "account_id": self.account_id, "name": self.name, "segment": self.segment,
            "sequence_key": self.sequence_key,
            "sequence_label": catalog.sequence_label(self.sequence_key),
            "fit_band": self.fit_band, "fit_label": self.fit_label,
            "heat_score": self.heat_score, "heat_tier": self.heat_tier,
            "intent_tier": self.intent_tier, "reasons": self.reasons,
        }


def _flat_signals(raw: list | None) -> list[dict]:
    """Map a scored account's carried discovery signals into the flat shape
    priority.intent expects (same mapping the lifecycle sweep uses)."""
    out: list[dict] = []
    for s in raw or []:
        if not isinstance(s, dict):
            continue
        p = s.get("payload") or {}
        out.append({
            "signal_type": s.get("signal_type"),
            "title": p.get("job_title"),
            "role": p.get("role"),
            "tier": p.get("tier"),
            "observed_at": s.get("observed_at"),
        })
    return out


def intent_tier_for(account: dict) -> str | None:
    """Carried-signal buying-intent tier ('hot'/'watch'), or None with no signals.
    Uses the SAME scorer as the Discovery panel so 'Hot' means one thing."""
    sigs = _flat_signals(account.get("discovery_signals"))
    if not sigs:
        return None
    return priority.intent(sigs).tier


def eligible_accounts(scored: list[dict], heat_by_id: dict[str, int], *,
                      exclude_ids: set[str] | None = None,
                      fit_bands: tuple[str, ...] = DEFAULT_FIT_BANDS,
                      heat_tiers: tuple[str, ...] = DEFAULT_HEAT_TIERS) -> list[Eligible]:
    """The ready-to-enroll list: every scored account that passes the rule,
    hottest first. `heat_by_id` is account_id -> engagement heat score;
    `exclude_ids` = accounts already enrolled (the ledger)."""
    exclude = exclude_ids or set()
    out: list[Eligible] = []
    for a in scored:
        aid = a.get("account_id")
        if not aid or aid in exclude:
            continue
        if (a.get("state") or "") != "scored":
            continue                                   # queued/scoring/error never enroll
        band = str(a.get("tier_band") or "").lower()
        if band not in fit_bands:
            continue                                   # low/out fit — not worth a sequence
        heat_score = int(heat_by_id.get(aid) or 0)
        heat_tier = engagement_scoring.tier_for(heat_score)
        intent = intent_tier_for(a)
        hot_heat = heat_tier in heat_tiers
        hot_intent = intent == "hot"
        if not (hot_heat or hot_intent):
            continue                                   # fit alone is not a trigger
        reasons = [f"{(a.get('tier_label') or band.title())} fit"]
        if hot_heat:
            reasons.append(f"{heat_tier} engagement ({heat_score} pts)")
        if hot_intent:
            reasons.append("Hot buying intent")
        out.append(Eligible(
            account_id=aid, name=a.get("name") or aid, segment=a.get("segment"),
            sequence_key=catalog.sequence_key_for(a), fit_band=band,
            fit_label=a.get("tier_label"), heat_score=heat_score, heat_tier=heat_tier,
            intent_tier=intent, reasons=reasons,
        ))
    out.sort(key=lambda e: (e.heat_score, e.fit_band == "high"), reverse=True)
    return out


def plan_contacts(contacts: list[dict], *, already: set[str] | None = None,
                  cap: int | None = None) -> tuple[list[dict], dict[str, int]]:
    """The contacts to actually push for one account: locked decision is ALL
    matched contacts, minus the un-sendable. Returns (planned, skipped_counts).

    Drops: no email (Reply.io keys on email) · opted out (never re-touch) ·
    already in this campaign per the ledger · duplicate emails (one send per
    human, whatever source the contact came from).
    """
    already = already or set()
    planned: list[dict] = []
    skipped = {"no_email": 0, "opted_out": 0, "already": 0, "duplicate": 0, "capped": 0}
    seen: set[str] = set()
    for c in contacts:
        email = (c.get("email") or "").strip().lower()
        ext = c.get("external_id")
        if not email:
            skipped["no_email"] += 1
            continue
        if c.get("opted_out"):
            skipped["opted_out"] += 1
            continue
        if ext in already:
            skipped["already"] += 1
            continue
        if email in seen:
            skipped["duplicate"] += 1
            continue
        if cap is not None and len(planned) >= cap:
            skipped["capped"] += 1
            continue
        seen.add(email)
        planned.append({"contact_ext": ext, "email": email,
                        "title": c.get("title"), "company": c.get("company")})
    return planned, skipped
