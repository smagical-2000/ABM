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
import os

from auto_search.engagement import identity, notify, scoring
from auto_search.ops import heartbeat

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
            slot = calc.setdefault(aid, {"score": 0, "click_pts": 0, "touch": None})
            slot["score"] += pts
            if kind in scoring.CLICK_KINDS:
                slot["click_pts"] += pts
            occ = _ts(e.get("occurred_at"))
            if pts > 0 and occ and (slot["touch"] is None or occ > slot["touch"]):
                slot["touch"] = occ
    # The board serves click-CAPPED heat (AGT-1453) — the independent recompute
    # must apply the same cap, or I3 would flag every clicked account as drift.
    for slot in calc.values():
        slot["score"] = scoring.capped_score(slot["score"], slot.pop("click_pts", 0))
    if drift:
        violations.append({"code": "I2-points",
                           "detail": f"stored points != canonical matrix: {drift}"})

    # I5 — every ingest must be followed by the identity self-heal. On
    # 2026-07-14 a stale discovery-cron container (built before the heal
    # existed) wrote rows straight to the shared Postgres and re-minted 15
    # twins; nothing in ITS process could heal them. This tripwire catches any
    # writer that bypasses the heal — old containers, manual SQL, new services
    # — forever. 5-minute tolerance covers same-run ordering.
    newest_ingest = _dt_or_none(max(
        (_ts(e.get("ingested_at")) for e in events), default=""))
    marker = {}
    if hasattr(engagement_repo, "get_setting"):
        marker = json.loads(engagement_repo.get_setting("identity_heal_last") or "{}")
    heal_at = _dt_or_none(str(marker.get("at") or ""))
    if newest_ingest and (heal_at is None
                          or (newest_ingest - heal_at).total_seconds() > 300):
        violations.append({"code": "I5-stale-heal",
                           "detail": f"newest ingest {newest_ingest} has no follow-up "
                                     f"self-heal (last heal: {heal_at or 'never'}) — a "
                                     "writer without the heal (stale container?) touched "
                                     "the store"})

    # I6 — fleet build parity (2026-07-20): every cron heartbeats its
    # BUILD_STAMP on run start, and the API heartbeats its own at boot. Any
    # writer that beat AFTER the API's beat on a DIFFERENT build is a stale
    # container caught on its first run, by name — not 10 days later via the
    # twins it mints (linkedin-tofu-cron, 2026-07-10→20). Anchoring on the
    # API's own beat (not a 24h window) means a routine deploy's lingering
    # pre-deploy beats can't false-red healthy crons. Skipped when either side
    # has no stamp (local/dev) or the API has never beaten (no anchor).
    own_stamp = (os.getenv("BUILD_STAMP") or "").strip()
    if own_stamp and own_stamp != "unset" and hasattr(engagement_repo, "get_setting"):
        stale = heartbeat.stale_writers(heartbeat.read_stamps(engagement_repo), own_stamp)
        if stale:
            violations.append({"code": "I6-fleet",
                               "detail": f"writers on a different build than the API "
                                         f"({own_stamp}): {stale} — redeploy them "
                                         "(scripts/ship.sh deploys the whole fleet)"})

    board = rows
    if board is None:
        # Bare engaged_accounts rows carry no display name and no lists; enrich
        # exactly like the board does (names from display_maps, lists from the
        # contacts' matched_lists union) — or I4's company grouping would
        # degrade to per-id keys and the ABM-only gate it now models (2026-07-23)
        # would silently drop every company on this path.
        names, _doms = identity.display_maps(scoring_repo, discovery_repo)
        lists_by: dict[str, set] = {}
        for c in engagement_repo.contacts():
            cid = c.get("account_id")
            if cid:
                lists_by.setdefault(cid, set()).update(c.get("matched_lists") or [])
        board = [{**r, "name": names.get(r.get("account_id"), r.get("account_id")),
                  "lists": sorted(lists_by.get(r.get("account_id"), set()))}
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
        # Ghosts: an account with scored events that the served board DROPPED
        # is invisible everywhere downstream — the purest false negative.
        ghosts = [aid for aid, got in calc.items()
                  if got["score"] > 0 and aid not in {r.get("account_id") for r in board}]
        if ghosts:
            violations.append({"code": "I3-ghost",
                               "detail": f"{len(ghosts)} accounts have scored events "
                                         f"but no tile: {ghosts[:5]}"})

    # I4 — the exact MAR2-32 failure shape: a company whose MERGED heat is due
    # while no single tile fires. Post-heal every group is a singleton, so this
    # passes trivially; it exists to catch the next divergence class.
    ledger = json.loads(engagement_repo.get_setting("notified_tiers") or "{}")
    cutoff = ((engagement_repo.get_setting("activation_cutoff") or "")
              .strip()[:10] or None)
    # Carry the TRIGGER clock + lists through the rebuild, and gate abm_only=True
    # on BOTH sides (MAR2-44 #1, 2026-07-23): the interlock must model the
    # production sender exactly — stripping last_real_touch would audit the
    # display clock the sender no longer reads, and leaving the ABM gate off
    # would flag companies the sender deliberately suppresses as "missing".
    tiles = [{"account_id": r.get("account_id"),
              "name": r.get("name") or r.get("account_id"),
              "tier": r.get("tier") or scoring.tier_for(int(r.get("score") or 0)),
              "last_touch": r.get("last_touch"),
              "last_real_touch": r.get("last_real_touch"),
              "lists": r.get("lists") or []} for r in board]
    score_of = {r.get("account_id"): int(r.get("score") or 0) for r in board}
    groups: dict[str, list[dict]] = {}
    for t in tiles:
        key = notify.company_key(t["name"]) or t["account_id"]
        groups.setdefault(key, []).append(t)
    due_tiles = {notify.company_key(d["account"].get("name") or "")
                 for d in notify.accounts_to_notify(tiles, ledger, cutoff=cutoff,
                                                    abm_only=True)}
    missing = []
    for key, g in groups.items():
        total = sum(score_of.get(t["account_id"], 0) for t in g)
        pseudo = {"account_id": g[0]["account_id"], "name": g[0]["name"],
                  "tier": scoring.tier_for(total),
                  "last_touch": max(t.get("last_touch") or "" for t in g) or None,
                  "last_real_touch": max(t.get("last_real_touch") or ""
                                         for t in g) or None,
                  "lists": sorted({x for t in g for x in (t.get("lists") or [])})}
        if (notify.accounts_to_notify([pseudo], ledger, cutoff=cutoff, abm_only=True)
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


def _dt_or_none(v):
    """Parse an ISO-ish timestamp (str or stringified datetime) to an aware
    datetime for arithmetic; None when blank or unparseable."""
    from datetime import UTC, datetime
    s = str(v or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace(" ", "T"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return None
