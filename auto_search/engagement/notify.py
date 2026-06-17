"""Engagement activation → Slack. Posts a hot-account card to an incoming webhook.

Split like the rest of the engagement code: `build_card` is PURE (account dict +
events -> Slack Block Kit payload, fully testable, no I/O); `post_card` does the one
bit of I/O (httpx POST to the webhook from SLACK_ENGAGEMENT_WEBHOOK).

SDR tagging is intentionally NOT live yet — we render the owner as plain text, never
an encoded <@id> mention, so nobody gets pinged while we tune the format. Flip
`mention_sdr` (with a real Slack user-id map) when the user says go.
"""

from __future__ import annotations

import logging
import os
import re
from collections import Counter

import httpx

logger = logging.getLogger(__name__)

# kind -> label (no emoji — the Slack card stays clean/professional)
_KIND = {
    "high_intent_lead": "High-intent lead",
    "meeting_booked": "Meeting",
    "opportunity": "Opportunity",
    "reply": "Reply",
    "click": "Click",
    "podcast_lead": "Podcast",
    "tradeshow": "Tradeshow",
    "low_intent_lead": "TOFU content",
}


def build_card(account: dict, events: list[dict], *, dms: list[dict] | None = None,
               research: dict | None = None, app_url: str | None = None,
               sdr: str | None = None, test: bool = False) -> dict:
    """Build the Slack message (Block Kit) for an activated account. PURE.

    `dms` are the enriched decision-makers (name/title/email/phone) — the sales
    packet rendered into the card. `research` is the SDR intel brief (why-now /
    triggers / news / opening angle) from `summarize_research` — reuses data we
    already have, no extra cost. `sdr` is rendered as PLAIN TEXT (no @-mention →
    no notification). `test` marks the message as a wiring test.
    """
    name = account.get("name") or account.get("account_id") or "Unknown account"
    tier = account.get("tier") or "—"
    score = account.get("score") or 0
    header = f"[TEST] {name} — {tier}" if test else f"{name} — {tier}"

    bits = [f"*Heat:* {score} pts ({tier})"]
    breakdown = _breakdown(events)
    if breakdown:
        bits.append(f"*Signals:* {breakdown}")
    cls = _classification(account)
    if cls:
        bits.append(f"*Classification:* {cls}")
    lists = account.get("lists") or []
    if lists:
        bits.append(f"*Lists:* {', '.join(lists)}")
    if account.get("domain"):
        bits.append(f"*Domain:* {account['domain']}")
    if sdr:
        bits.append(f"*SDR:* {sdr}")        # plain text — not a mention

    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": header[:150]}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(bits)}},
    ]

    intel = _research_lines(research)
    if intel:
        blocks.append({"type": "divider"})
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn", "text": "*Account intel*\n" + intel}})

    dm_lines = _dm_lines(dms)
    if dm_lines:
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn", "text": "*Decision-makers*\n" + dm_lines}})
    if app_url and app_url.startswith(("http://", "https://")):   # Slack rejects scheme-less URLs
        blocks.append({"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "Open in console"},
             "url": app_url}]})

    fallback = f"{name} is {tier} ({score} pts)"
    return {"text": fallback, "blocks": blocks}


def post_card(payload: dict, *, webhook: str | None = None,
              http: httpx.Client | None = None) -> bool:
    """POST a prebuilt payload to the Slack incoming webhook. Returns True on 2xx.
    Webhook from SLACK_ENGAGEMENT_WEBHOOK unless passed explicitly."""
    hook = webhook or os.getenv("SLACK_ENGAGEMENT_WEBHOOK")
    if not hook:
        logger.warning("SLACK_ENGAGEMENT_WEBHOOK not set — skipping activation post")
        return False
    try:
        resp = (http.post(hook, json=payload) if http is not None
                else httpx.post(hook, json=payload, timeout=15))
        ok = 200 <= resp.status_code < 300
        if not ok:
            logger.warning("slack webhook -> %s: %s", resp.status_code, resp.text[:200])
        return ok
    except Exception:  # noqa: BLE001 — activation must never crash the caller
        logger.exception("slack webhook post failed")
        return False


def activate_account(account: dict, events: list[dict], *, dms: list[dict] | None = None,
                     research: dict | None = None, app_url: str | None = None,
                     sdr: str | None = None, test: bool = False,
                     webhook: str | None = None, http: httpx.Client | None = None) -> bool:
    """Build + post the activation card (with enriched decision-makers + intel brief).
    Returns True if Slack accepted it."""
    return post_card(build_card(account, events, dms=dms, research=research,
                                app_url=app_url, sdr=sdr, test=test),
                     webhook=webhook, http=http)


def _dm_lines(dms: list[dict] | None, *, limit: int = 5) -> str:
    """Up to `limit` decision-makers: '• *Jane Doe* — VP Revenue Cycle\\n   jane@x.com · +1…'."""
    out = []
    for p in (dms or [])[:limit]:
        who = p.get("name") or "—"
        title = f" — {p['title']}" if p.get("title") else ""
        ci = " · ".join(x for x in (p.get("email"), p.get("phone")) if x) or "no contact info found"
        out.append(f"• *{who}*{title}\n   {ci}")
    return "\n".join(out)


# ── helpers ──────────────────────────────────────────────────────────────


def _classification(account: dict) -> str | None:
    """Human classification: scored framework + fit tier when present, else the ABM
    segment (junk values already suppressed upstream)."""
    parts = []
    fw = account.get("framework")
    if fw:
        parts.append({"specialty": "Specialty", "health_system": "Health System",
                      "payer": "Payer"}.get(fw, fw))
    fit = account.get("fit_tier") or account.get("tier_label")
    if fit:
        parts.append(str(fit))
    seg = account.get("segment")
    if seg and str(seg) not in _JUNK_SEGMENTS and not parts:
        parts.append(str(seg))
    return " · ".join(parts) if parts else None


# ABM-import artifacts (sheet/tab names) — never show these as a segment. The API
# already cleans segments before the card is built; this is a defensive backstop.
_JUNK_SEGMENTS = frozenset({"Matches", "Sheet30"})


def _breakdown(events: list[dict]) -> str:
    """Per-kind counts only (no per-touch spam): 'High-intent lead 1 · Click 2 · Reply 1'.
    Events are one-per-contact×kind, so the count is meaningful. Ordered by weight."""
    if not events:
        return ""
    counts = Counter(e.get("kind") for e in events)
    return " · ".join(f"{_KIND.get(k, k or 'Touch')} {n}" for k, n in counts.most_common())


# ── SDR intel brief (deep-research, reuses already-stored data) ───────────────


def summarize_research(scored: dict | None, *, max_signals: int = 3,
                       max_news: int = 2) -> dict:
    """SDR-ready intel from an account's EXISTING research — no live calls, no cost.

    Pulls the scored account's discovery signals (the triggers that put it in the
    funnel: hiring, funding, layoffs, leadership) and the Claude dossier (entry
    timing = 'why now', recent news, recommended opening angle). PURE. Returns {}
    when the account has no stored research (e.g. ABM-only, never scored)."""
    # isinstance (not truthiness): legacy/migrated JSONB could be a non-dict and
    # must never crash an activation post.
    s = scored if isinstance(scored, dict) else {}
    dossier = s.get("dossier") if isinstance(s.get("dossier"), dict) else {}
    entry = (dossier.get("entry_strategy")
             if isinstance(dossier.get("entry_strategy"), dict) else {})
    out: dict = {}

    why = _clean(entry.get("timing"))
    if why:
        out["why_now"] = _trim(why, 240)

    signals: list[str] = []
    seen: set[str] = set()
    for sig in _as_list(s.get("discovery_signals")):
        txt = _clean(sig.get("summary") if isinstance(sig, dict) else sig)
        if not txt:
            continue
        # discovery often repeats one role across many locations ("Hiring: X — City");
        # dedupe on the head so the brief shows distinct triggers, not the same one.
        key = re.split(r"\s[—–-]\s", txt, maxsplit=1)[0].strip().lower()
        if key in seen:
            continue
        seen.add(key)
        signals.append(_trim(txt, 120))
        if len(signals) >= max_signals:
            break
    if signals:
        out["triggers"] = signals

    news: list[dict] = []
    for n in _as_list(dossier.get("recent_news")):
        if not isinstance(n, dict):
            continue
        head = _clean(n.get("headline"))
        # dossiers record negative findings ("No significant expansion identified…") —
        # those aren't news a rep can use on a call, so skip them.
        if not head or head.lower().startswith("no "):
            continue
        news.append({"headline": _trim(head, 140), "date": _clean(n.get("date"))})
        if len(news) >= max_news:
            break
    if news:
        out["news"] = news

    angles = _as_list(entry.get("primary_angles"))
    angle = _clean(angles[0]) if angles else ""
    if angle:
        out["angle"] = _trim(angle, 240)

    return out


def _as_list(v) -> list:
    """v if it's a list, else [] — JSONB fields can be the wrong shape."""
    return v if isinstance(v, list) else []


def _research_lines(research: dict | None) -> str:
    """Render the intel brief as mrkdwn (no emoji). Empty string when nothing."""
    if not research:
        return ""
    out: list[str] = []
    if research.get("why_now"):
        out.append(f"*Why now:* {research['why_now']}")
    triggers = research.get("triggers") or []
    if triggers:
        out.append("*Triggers:*\n" + "\n".join(f"• {x}" for x in triggers))
    news = research.get("news") or []
    if news:
        lines = [f"• {n['headline']}" + (f" ({n['date']})" if n.get("date") else "")
                 for n in news]
        out.append("*Recent news:*\n" + "\n".join(lines))
    if research.get("angle"):
        out.append(f"*Opening angle:* {research['angle']}")
    return "\n".join(out)


def _clean(v) -> str:
    """str | None -> stripped str (empty for None/blank)."""
    return str(v).strip() if v is not None else ""


def _trim(s: str, n: int) -> str:
    """Truncate to n chars with an ellipsis (Slack sections cap at 3000)."""
    s = s.strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"
