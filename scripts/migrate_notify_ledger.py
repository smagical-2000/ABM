"""One-time migration: notify ledger keys account_id -> company key (MAR2-31).

Why: bulk imports mint new internal account ids (csv_/acc_) for companies that
already had engagement history under abm_ ids. The notify ledger was keyed by
account id, so on 2026-07-09 the notifier read 83 already-handled companies as
brand-new tier rises. The ledger is now keyed by company identity
(notify.company_key); this migrates the existing entries.

Method — names, not string surgery:
  * abm_<key> ids embed the canonical normalized name key (that is literally how
    cross.py mints them), and the ABM target list maps key -> real name.
  * csv_/acc_ ids resolve to names through the scored-accounts store.
  * An id that resolves nowhere KEEPS its original key — reads still work via
    the notifier's account-id fallback, and a later re-run migrates it if it
    becomes resolvable. Nothing is guessed.

Collisions (several old ids -> one company key) keep the STRONGEST state
(highest tier, then newest touch): the conservative direction — a merged entry
can only suppress a duplicate alert, never invent one.

Idempotent: entries already under a company key are kept as-is; re-running
converges. A durable backup of the pre-migration ledger must exist (setting
`notified_tiers_backup_2026_07_09`) — the script refuses to apply without it.

Usage:
    python scripts/migrate_notify_ledger.py            # dry-run: report only
    python scripts/migrate_notify_ledger.py --apply    # write the migrated ledger
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv()   # no override: an operator-exported DATABASE_URL must win

from auto_search.engagement import notify  # noqa: E402
from auto_search.normalize import normalize_company_name  # noqa: E402


def _tier_rank(t: str | None) -> int:
    return {"lower": 0, "some": 1, "warm": 2, "hot": 3}.get((t or "").lower(), 0)


def _stronger(a: dict, b: dict) -> dict:
    """Collision rule — the ONE shared implementation (notify.stronger_state),
    so migration, seed, and send can never disagree about which state wins."""
    return notify.stronger_state(a, b)


def _as_dict(v, source_id: str) -> dict:
    """Normalize a ledger value (dict or legacy bare tier string) to the dict
    form, carrying the source account id for auditability."""
    if isinstance(v, dict):
        out = dict(v)
    else:
        out = {"tier": str(v) if v else "Lower", "touch": None}
    out.setdefault("account_id", source_id)
    return out


def build_name_index(scoring_repo, discovery_repo) -> dict[str, str]:
    """account_id -> real company name, from the systems that minted the ids."""
    idx: dict[str, str] = {}
    # scored accounts: csv_/acc_ (and any other scored ids) carry their names
    try:
        for a in scoring_repo.list_accounts():
            if a.get("account_id") and a.get("name"):
                idx[a["account_id"]] = a["name"]
    except Exception:  # noqa: BLE001 — store unreachable: abm_ resolution still works
        pass
    # ABM targets: abm_<canonical key> is minted from the target's name
    try:
        for t in discovery_repo.abm_targets():
            name = t.get("name")
            if name:
                idx[f"abm_{normalize_company_name(name)}"] = name
    except Exception:  # noqa: BLE001
        pass
    return idx


def migrate(ledger: dict, names: dict[str, str]) -> tuple[dict, dict]:
    """Return (new_ledger, report). Pure — no I/O."""
    migrated: dict = {}
    report = {"company_keyed": 0, "migrated": 0, "kept_unresolved": 0,
              "collisions": [], "already_company_key": 0}

    def _put(key: str, entry: dict, src: str):
        if key in migrated:
            keep = _stronger(migrated[key], entry)
            report["collisions"].append(
                {"key": key, "kept": keep.get("account_id"),
                 "dropped": (entry if keep is not entry else migrated[key]).get("account_id")})
            migrated[key] = keep
        else:
            migrated[key] = entry

    for k, v in ledger.items():
        entry = _as_dict(v, k)
        name = names.get(k)
        if name:
            ck = notify.company_key(name)
            if ck:
                entry.setdefault("name", name)
                _put(ck, entry, k)
                report["migrated"] += 1
                continue
        # Not resolvable to a name. If the key LOOKS like it is already a
        # company key (no scheme prefix), keep it verbatim; else keep the
        # account-id key as-is — the notifier's fallback still honors it.
        if "_" not in k:
            _put(k, entry, k)
            report["already_company_key"] += 1
        else:
            _put(k, entry, k)
            report["kept_unresolved"] += 1
    return migrated, report


def main() -> int:
    ap = argparse.ArgumentParser(description="Migrate notify ledger to company keys")
    ap.add_argument("--apply", action="store_true", help="write the migrated ledger")
    args = ap.parse_args()

    from auto_search.db import get_repository
    from auto_search.db.engagement_repository import get_engagement_repository
    from auto_search.db.scoring_repository import get_scoring_repository

    repo = get_engagement_repository()
    ledger = json.loads(repo.get_setting("notified_tiers") or "{}")
    print(f"[migrate] current ledger entries: {len(ledger)}")

    backup = repo.get_setting("notified_tiers_backup_2026_07_09")
    if args.apply and not backup:
        print("[migrate] REFUSING to apply: backup setting "
              "'notified_tiers_backup_2026_07_09' not found. Back up first.")
        return 1
    if args.apply:
        # Apply-safety (QA panel): (1) the backup must actually contain a
        # ledger; (2) the live ledger may have moved since this process read it
        # (a card posted mid-run) — re-read fresh RIGHT before transforming, so
        # the write below is a transform of the newest state, and snapshot that
        # exact pre-write state under a timestamped backup key.
        import datetime as _dt
        try:
            if not isinstance(json.loads(backup), dict):
                raise ValueError
        except ValueError:
            print("[migrate] REFUSING to apply: backup content is not a ledger.")
            return 1
        fresh = repo.get_setting("notified_tiers") or "{}"
        if fresh != json.dumps(ledger) and json.loads(fresh) != ledger:
            print("[migrate] live ledger moved since first read — using the fresh copy")
            ledger = json.loads(fresh)
        stamp = _dt.datetime.now(_dt.UTC).strftime("%Y%m%dT%H%M%SZ")
        repo.set_setting(f"notified_tiers_prewrite_{stamp}", json.dumps(ledger))

    names = build_name_index(get_scoring_repository(), get_repository())
    print(f"[migrate] name index: {len(names)} account ids resolvable")

    new_ledger, report = migrate(ledger, names)
    print(f"[migrate] migrated to company keys: {report['migrated']} | "
          f"already company-keyed: {report['already_company_key']} | "
          f"kept unresolved (id fallback still works): {report['kept_unresolved']} | "
          f"collisions merged (strongest kept): {len(report['collisions'])}")
    for c in report["collisions"][:10]:
        print(f"    collision {c['key']}: kept {c['kept']}, absorbed {c['dropped']}")
    print(f"[migrate] new ledger entries: {len(new_ledger)}")

    if not args.apply:
        print("[migrate] dry-run only — nothing written. --apply to write.")
        return 0
    repo.set_setting("notified_tiers", json.dumps(new_ledger))
    check = json.loads(repo.get_setting("notified_tiers") or "{}")
    print(f"[migrate] WRITTEN and verified: {len(check)} entries.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
