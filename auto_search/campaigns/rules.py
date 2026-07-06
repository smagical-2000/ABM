"""Routing rules — PURE. Which campaign does a qualifying account go to?

The operator's mental model, made literal: every route is a RULE.
  • The 7 GROUP rules are built in (catalog.SEQUENCE_KEYS -> the group's mapped
    campaign) — the fallback everyone understands.
  • CUSTOM rules refine routing on top: "Hot accounts -> fast-track campaign",
    "Warm Health Systems -> event invite". First matching custom rule wins;
    no match falls through to the account's group rule.

Custom rules NARROW ROUTING, never eligibility: the base gate (scored + in-
market, enroll.py) still decides WHO qualifies; rules only decide WHERE a
qualifying account is sent. Stored as JSON (engagement settings key
`campaigns_custom_rules`); this module owns the shape, validation, and matching
so the runner and the board preview can never disagree.
"""

from __future__ import annotations

import json
import uuid

from auto_search.campaigns import catalog

SETTING_KEY = "campaigns_custom_rules"

_HEAT_RANK = {"any": 0, "some": 1, "warm": 2, "hot": 3}
_TIER_RANK = {"Lower": 0, "Some": 1, "Warm": 2, "Hot": 3}
FIT_CHOICES = ("high", "high_med")               # High only | High + Medium


def parse(raw: str | None) -> list[dict]:
    """Stored JSON -> validated rule list (bad entries dropped, never raised)."""
    try:
        rows = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [r for r in (normalize(x) for x in rows if isinstance(x, dict)) if r]


def normalize(r: dict) -> dict | None:
    """One rule -> canonical shape, or None if unusable. Unknown group keys and
    malformed fields are cleaned rather than trusted."""
    name = str(r.get("name") or "").strip()
    if not name:
        return None
    heat_min = str(r.get("heat_min") or "any").lower()
    if heat_min not in _HEAT_RANK:
        heat_min = "any"
    fit = str(r.get("fit") or "high_med").lower()
    if fit not in FIT_CHOICES:
        fit = "high_med"
    groups = [g for g in (r.get("groups") or []) if g in catalog.SEQUENCE_KEYS]
    return {
        "id": str(r.get("id") or uuid.uuid4().hex[:8]),
        "name": name[:60],
        "enabled": bool(r.get("enabled", True)),
        "heat_min": heat_min,                    # any | some | warm | hot
        "fit": fit,                              # high | high_med
        "groups": groups,                        # [] = any group
        "campaign_id": (str(r["campaign_id"]) if r.get("campaign_id") else None),
        "campaign_name": r.get("campaign_name"),
        "li_campaign_id": (str(r["li_campaign_id"]) if r.get("li_campaign_id") else None),
        "li_campaign_name": r.get("li_campaign_name"),
    }


def matches(rule: dict, e) -> bool:
    """Does an Eligible account satisfy a rule's conditions? All specified
    conditions must hold (AND); an empty condition means 'any'."""
    if not rule.get("enabled", True):
        return False
    if _TIER_RANK.get(e.heat_tier, 0) < _HEAT_RANK[rule.get("heat_min", "any")]:
        # heat condition can also be satisfied by Hot INTENT when the rule asks
        # for hot — an intent-hot account is "hot" in the operator's sense.
        if not (rule.get("heat_min") == "hot" and e.intent_tier == "hot"):
            return False
    if rule.get("fit") == "high" and e.fit_band != "high":
        return False
    groups = rule.get("groups") or []
    if groups and e.sequence_key not in groups:
        return False
    return True


def resolve_route(e, custom_rules: list[dict], email_map: dict[str, dict],
                  li_map: dict[str, dict]) -> dict:
    """The single routing decision for one eligible account — used by BOTH the
    runner and the board preview so what you see is what sends.

    Returns {rule (None = group rule), email: {campaign_id, campaign_name}|None,
             linkedin: {campaign_id, campaign_name}|None, route_label}.
    A custom rule missing its own campaign falls back to the group's for that
    channel, so a half-filled rule degrades instead of black-holing accounts.
    """
    group_email = email_map.get(e.sequence_key)
    group_li = li_map.get(e.sequence_key)
    for rule in custom_rules:
        if matches(rule, e):
            email = ({"campaign_id": rule["campaign_id"],
                      "campaign_name": rule.get("campaign_name")}
                     if rule.get("campaign_id") else group_email)
            li = ({"campaign_id": rule["li_campaign_id"],
                   "campaign_name": rule.get("li_campaign_name")}
                  if rule.get("li_campaign_id") else group_li)
            return {"rule": rule, "email": email, "linkedin": li,
                    "route_label": rule["name"]}
    return {"rule": None, "email": group_email, "linkedin": group_li,
            "route_label": catalog.sequence_label(e.sequence_key)}
