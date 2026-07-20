"""Daily ops digest — one card, every weekday, so silence is never ambiguous.

Born 2026-07-20 after a weekend where every Zapier card in the company channel
triggered a manual forensic session ("was this captured? scored? why no
alert?"). The digest answers those questions BEFORE they're asked: what synced,
what came in, what's due (and would it be held), what's unresolved, and whether
every service runs the same build.

Design rules (the 2026-07-20 review):
  - The due section READS the one evaluator (GET /api/engagement/due) over
    HTTP — it never re-implements accounts_to_notify (the hand-built-send-list
    drift class). Missing env / a dead API renders "due: unavailable (<why>)".
  - Every section builds independently and the assembler isolates each in its
    own try/except, so ONE broken section can never kill the whole digest.
  - The Slack post goes through notify.post_card so a dead webhook logs its
    status instead of silently "succeeding".

Best-effort: never exits non-zero (the daily run must not fail on reporting).
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from dotenv import load_dotenv

from auto_search.db.engagement_repository import get_engagement_repository
from auto_search.engagement import notify
from auto_search.ops import heartbeat

load_dotenv()
logger = logging.getLogger("run_digest")


def _sec_sync(erepo) -> str:
    """1) sync freshness per source — a stale source names itself."""
    lines = []
    for src in ("linkedin_tofu", "sfdc", "replyio", "podcast"):
        st = erepo.get_sync_state(src) if hasattr(erepo, "get_sync_state") else None
        at = str((st or {}).get("last_synced_at") or "never")[:16]
        status = (st or {}).get("status") or "—"
        lines.append(f"• `{src}` last sync {at} ({status})")
    return "\n".join(lines)


def _sec_ingested(erepo) -> str:
    """2) touches ingested in the last 24h, by source."""
    day_ago = (datetime.now(UTC) - timedelta(hours=24)).isoformat()
    by_src: dict[str, int] = {}
    for e in erepo.recent_events(limit=5000):
        if str(e.get("ingested_at") or "") >= day_ago:
            by_src[e.get("source") or "?"] = by_src.get(e.get("source") or "?", 0) + 1
    return "• last 24h ingested: " + (", ".join(
        f"{k} {v}" for k, v in sorted(by_src.items())) or "none")


def _sec_due() -> str:
    """3) due-for-activation — read from THE evaluator (GET /api/engagement/due),
    exactly like run_engagement_notify.py talks to the API. Never re-implemented
    here, and never a crash: no env / a dead API renders as unavailable."""
    base = (os.getenv("ENGAGEMENT_APP_URL") or "").rstrip("/")
    if not base:
        return "• due: unavailable (ENGAGEMENT_APP_URL not set)"
    user, pw = os.getenv("ENGAGEMENT_API_USER"), os.getenv("ENGAGEMENT_API_PASS")
    auth = (user, pw) if user and pw else None
    try:
        r = httpx.get(f"{base}/api/engagement/due", auth=auth, timeout=120)
        r.raise_for_status()
        d = r.json()
    except Exception as e:  # noqa: BLE001 — the digest renders regardless
        return f"• due: unavailable ({e})"
    top = ", ".join(str(x.get("account")) for x in (d.get("detail") or [])[:8])
    line = f"• due for activation: *{d.get('due')}*"
    if d.get("held"):
        line += " — HELD; review /api/engagement/due"
    if top:
        line += f" — top: {top}"
    return line + f" · stage=`{d.get('stage')}`"


def _sec_unresolved(erepo) -> str:
    """4) unresolved contacts (the silent-miss queue)."""
    unresolved = erepo.contacts(unresolved_only=True)
    doms: dict[str, int] = {}
    for c in unresolved[:400]:
        d = (c.get("email_domain") or "").strip().lower()
        if d:
            doms[d] = doms.get(d, 0) + 1
    top_doms = ", ".join(f"{d}×{n}" for d, n in
                         sorted(doms.items(), key=lambda kv: -kv[1])[:5])
    return (f"• unresolved contacts: {len(unresolved)}"
            + (f" — top domains: {top_doms}" if top_doms else ""))


def _sec_fleet(erepo) -> str:
    """5) fleet build parity (I6) — same reader the audit uses."""
    stamps = heartbeat.read_stamps(erepo)
    own = os.getenv("BUILD_STAMP", "unset")
    fleet = ", ".join(f"{svc}:{(rec or {}).get('stamp', '?')}"
                      for svc, rec in sorted(stamps.items())) or "no heartbeats yet"
    return f"• builds — this run: `{own}` · fleet: {fleet}"


def build_digest() -> str:
    erepo = get_engagement_repository()
    sections = (("sync", lambda: _sec_sync(erepo)),
                ("ingested", lambda: _sec_ingested(erepo)),
                ("due", _sec_due),
                ("unresolved", lambda: _sec_unresolved(erepo)),
                ("fleet", lambda: _sec_fleet(erepo)))
    lines: list[str] = []
    for name, build in sections:
        # One bad section must never kill the digest — the digest going missing
        # IS the alarm, so it only ever degrades a line at a time.
        try:
            lines.append(build())
        except Exception as e:  # noqa: BLE001
            logger.exception("digest section %s failed", name)
            lines.append(f"• {name} unavailable: {e}")
    return "\n".join(lines)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        body = build_digest()
    except Exception:
        logger.exception("digest build failed")
        return 0
    print("[digest]\n" + body, flush=True)
    if not os.getenv("SLACK_ENGAGEMENT_WEBHOOK"):
        print("[digest] SLACK_ENGAGEMENT_WEBHOOK unset — printed only", flush=True)
        return 0
    card = {"text": "Daily ABM digest",
            "blocks": [
                {"type": "header", "text": {"type": "plain_text",
                 "text": f"[DIGEST] ABM daily — {datetime.now(UTC).strftime('%b %d')}"}},
                {"type": "section", "text": {"type": "mrkdwn", "text": body[:2900]}}]}
    # post_card logs the webhook's status on failure — a dead webhook must not
    # look like a delivered digest.
    ok = notify.post_card(card)
    print(f"[digest] slack post {'ok' if ok else 'FAILED (see log)'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
