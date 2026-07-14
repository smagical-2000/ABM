"""Engagement trust monitor — standing invariants (MAR2-32).

Incident-driven QA pins failures somebody has already SEEN; a false negative —
an alert that silently never fires — leaves no artifact to pin. This module
recomputes ground truth from raw events on demand and checks that what the
system SHOWS (tiles) and what it would SEND (the due queue) agree with it:

  I1-twins      no auto-mergeable identity split exists (heal dry-run is empty)
  I2-points     every stored event's points match the canonical scoring matrix
  I3-recompute  every tile's score + last_touch equal an independent recompute
  I4-diverge    company-level due == tile-level due (the MAR2-32 silent class)

The notify endpoint HOLDS whenever ok is False (fail-safe): cards only ever
send from a board that just proved itself consistent. Domain-conflict twins
(the Healthfirst hf.org/healthfirst.org class) surface as manual_review, not
violations — a standing red light nobody can clear trains people to ignore it.
"""

from __future__ import annotations

import json

from auto_search.engagement import identity, notify, scoring

# recent_events() is the one full-scan primitive both repos share; cap it so a
# pathological store can't stall the notifier. At the cap, I3 is skipped (never
# false-alarmed) and the report says so.
_EVENTS_SCAN_CAP = 250_000


def run_invariants(engagement_repo, scoring_repo, discovery_repo, *,
                   rows: list[dict] | None = None) -> dict:
    """Check the four invariants. Read-only. Returns
    {"ok", "violations": [{code, detail}], "manual_review", "stats"}."""
    violations: list[dict] = []

    heal = identity.heal_identity_splits(engagement_repo, scoring_repo,
                                         discovery_repo, dry_run=True)
    if heal.get("merged"):
        violations.append({"code": "I1-twins",
                           "detail": f"unhealed identity splits: {heal['merged']}"})

    events = (engagement_repo.recent_events(limit=_EVENTS_SCAN_CAP)
              if hasattr(engagement_repo, "recent_events") else [])
    truncated = len(events) >= _EVENTS_SCAN_CAP
    drift: dict[str, int] = {}
    calc: dict[str, dict] = {}
    for e in events:
        kind = e.get("kind")
        if kind in scoring.DEPRECATED_KINDS:
            continue
        pts = int(e.get("points") or 0)
        if pts != scoring.points_for(kind):
            drift[kind] = drift.get(kind, 0) + 1
        aid = e.get("account_id")
        if aid:
            slot = calc.setdefault(aid, {"score": 0, "touch": None})
            slot["score"] += pts
            occ = _ts(e.get("occurred_at"))
            if pts > 0 and occ and (slot["touch"] is None or occ > slot["touch"]):
                slot["touch"] = occ
    if drift:
        violations.append({"code": "I2-points",
                           "detail": f"stored points != canonical matrix: {drift}"})

    board = rows
    if board is None:
        # Bare engaged_accounts rows carry no display name; enrich exactly like
        # the board does, or I4's company grouping would degrade to per-id keys.
        names, _doms = identity.display_maps(scoring_repo, discovery_repo)
        board = [{**r, "name": names.get(r.get("account_id"), r.get("account_id"))}
                 for r in engagement_repo.engaged_accounts()]
    if not truncated:
        bad = []
        for r in board:
            aid = r.get("account_id")
            got = calc.get(aid, {"score": 0, "touch": None})
            served = int(r.get("score") or 0)
            if served != got["score"] or _ts(r.get("last_touch")) != (got["touch"] or ""):
                bad.append((aid, served, got["score"],
                            _ts(r.get("last_touch")), got["touch"]))
        if bad:
            violations.append({"code": "I3-recompute",
                               "detail": f"{len(bad)} tiles diverge from event "
                                         f"recompute, e.g. {bad[:3]}"})

    # I4 — the exact MAR2-32 failure shape: a company whose MERGED heat is due
    # while no single tile fires. Post-heal every group is a singleton, so this
    # passes trivially; it exists to catch the next divergence class.
    ledger = json.loads(engagement_repo.get_setting("notified_tiers") or "{}")
    cutoff = ((engagement_repo.get_setting("activation_cutoff") or "")
              .strip()[:10] or None)
    tiles = [{"account_id": r.get("account_id"),
              "name": r.get("name") or r.get("account_id"),
              "tier": r.get("tier") or scoring.tier_for(int(r.get("score") or 0)),
              "last_touch": r.get("last_touch")} for r in board]
    score_of = {r.get("account_id"): int(r.get("score") or 0) for r in board}
    groups: dict[str, list[dict]] = {}
    for t in tiles:
        key = notify.company_key(t["name"]) or t["account_id"]
        groups.setdefault(key, []).append(t)
    due_tiles = {notify.company_key(d["account"].get("name") or "")
                 for d in notify.accounts_to_notify(tiles, ledger, cutoff=cutoff)}
    missing = []
    for key, g in groups.items():
        total = sum(score_of.get(t["account_id"], 0) for t in g)
        pseudo = {"account_id": g[0]["account_id"], "name": g[0]["name"],
                  "tier": scoring.tier_for(total),
                  "last_touch": max(t.get("last_touch") or "" for t in g) or None}
        if (notify.accounts_to_notify([pseudo], ledger, cutoff=cutoff)
                and key not in due_tiles):
            missing.append(g[0]["name"])
    if missing:
        violations.append({"code": "I4-diverge",
                           "detail": f"company-level due but no tile fires: "
                                     f"{missing[:6]}"})

    return {"ok": not violations, "violations": violations,
            "manual_review": heal.get("manual", []),
            "stats": {"tiles": len(board), "events_scanned": len(events),
                      "events_truncated": truncated}}


def _ts(v) -> str:
    """Normalize timestamps for comparison: datetimes and ISO strings, T or
    space separator, with/without fractional seconds all reduce to
    'YYYY-MM-DD HH:MM:SS'."""
    return str(v or "").replace("T", " ")[:19]
