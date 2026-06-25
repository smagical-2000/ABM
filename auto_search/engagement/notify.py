"""Engagement activation → Slack. Posts a hot-account card to an incoming webhook.

Split like the rest of the engagement code: `build_card` is PURE (account dict +
events -> Slack Block Kit payload, fully testable, no I/O); `post_card` does the one
bit of I/O (httpx POST to the webhook from SLACK_ENGAGEMENT_WEBHOOK).

Routing has two modes, gated by `live_routing()` (ENGAGEMENT_LIVE_ROUTING):
  • OFF (default) — every card posts to SLACK_ENGAGEMENT_WEBHOOK (the private testing
    line) with plain-text "@Name" owners (no real ping). Safe for testing.
  • ON — Hot cards route to SLACK_AE_WEBHOOK and Warm/Some to SLACK_SDR_WEBHOOK, and
    the owner becomes a real `<@id>` ping (from AE_SLACK_IDS / SDR_SLACK_IDS).
The endpoint passes ids={} (plain names) + webhook=None whenever it is not live, so
nobody in a real channel is disturbed until the flag is flipped.
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
    "high_intent_lead": "BOFU",
    "meeting_booked": "Meeting booked",
    "opportunity": "Opportunity",
    "reply": "Reply",
    "click": "Click",
    "podcast_lead": "Podcast",
    "tradeshow": "Tradeshow",
    "low_intent_lead": "TOFU lead (form)",
    "linkedin_tofu": "TOFU lead (LinkedIn ad)",
}


def build_card(account: dict, events: list[dict], *, dms: list[dict] | None = None,
               research: dict | None = None, app_url: str | None = None,
               sdr: str | None = None, ae: str | None = None, dm_limit: int = 5,
               test: bool = False) -> dict:
    """Build the Slack message (Block Kit) for an activated account. PURE.

    `dms` are the enriched decision-makers (name/title/email/phone) — the sales
    packet rendered into the card. `research` is the SDR intel brief (why-now /
    triggers / news / opening angle) from `summarize_research` — reuses data we
    already have, no extra cost. `ae` is the resolved owner reference for the lead
    line ("<@U…> your account X — move to status Hot"); pass a `<@id>` mention to
    actually ping, or a plain "@Name" to name them without a notification. `sdr` is
    rendered as PLAIN TEXT (no @-mention). `test` marks the message as a wiring test.
    """
    name = account.get("name") or account.get("account_id") or "Unknown account"
    tier = account.get("tier") or "—"
    score = account.get("score") or 0
    header = f"[TEST] {name} — {tier}" if test else f"{name} — {tier}"

    bits = []
    if ae:   # lead line — the AE call to action, tagged when a Slack id is known
        bits.append(f"{ae} your account *{name}* — move to status {tier}")
    bits.append(f"*Heat:* {score} pts ({tier})")
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

    dm_lines = _dm_lines(dms, limit=dm_limit)
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
                     sdr: str | None = None, ae: str | None = None, dm_limit: int = 5,
                     test: bool = False, webhook: str | None = None,
                     http: httpx.Client | None = None) -> bool:
    """Build + post the activation card (with enriched decision-makers + intel brief).
    Returns True if Slack accepted it."""
    return post_card(build_card(account, events, dms=dms, research=research,
                                app_url=app_url, sdr=sdr, ae=ae, dm_limit=dm_limit,
                                test=test),
                     webhook=webhook, http=http)


# ── AE routing (Hot account → owner) ─────────────────────────────────────────
# Two operator-maintained maps, both env-driven so they change without a deploy:
#   AE_SLACK_IDS   "Alykhan Jina=U01ABC;Manu Gupta=U02DEF"   (name -> Slack member id)
#   SPECIALTY_AE   "health_system=Alykhan Jina;payer=Manu Gupta;specialty=…"
# SFDC account owner (when known) wins over the specialty fallback. Without a Slack
# id we render a PLAIN "@Name" (names them, does not ping) — fill AE_SLACK_IDS to ping.


def _parse_pairs(raw: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in (raw or "").split(";"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            if k.strip() and v.strip():
                out[k.strip()] = v.strip()
    return out


def ae_slack_ids() -> dict[str, str]:
    return _parse_pairs(os.getenv("AE_SLACK_IDS"))


def specialty_ae() -> dict[str, str]:
    return _parse_pairs(os.getenv("SPECIALTY_AE"))


def resolve_ae(account: dict, *, owner_name: str | None = None,
               ids: dict[str, str] | None = None,
               by_specialty: dict[str, str] | None = None) -> str | None:
    """Owner reference for the lead line, or None if we can't name one.

    Prefer the SFDC account owner; else fall back to the AE assigned to the
    account's framework (health_system / specialty / payer). Returns a `<@id>`
    Slack mention when the id is known (a real ping), else a plain "@Name"."""
    ids = ae_slack_ids() if ids is None else ids   # {} = deliberately no pings (test mode)
    by_specialty = by_specialty if by_specialty is not None else specialty_ae()
    # `framework_key` is the raw rubric key (health_system/specialty/payer); fall back to
    # `framework` for callers that pass the raw key directly. SPECIALTY_AE is keyed by it.
    fw_key = account.get("framework_key") or account.get("framework") or ""
    # Order: explicit SFDC owner → the AE for this framework → DEFAULT_AE catch-all. The
    # catch-all means an unscored (no-framework) Hot account still tags someone, instead
    # of silently going untagged.
    name = (owner_name or "").strip() or by_specialty.get(fw_key) or _env_name("DEFAULT_AE")
    if not name:
        return None
    sid = ids.get(name)
    return f"<@{sid}>" if sid else f"@{name}"


def sdr_slack_ids() -> dict[str, str]:
    return _parse_pairs(os.getenv("SDR_SLACK_IDS"))


def specialty_sdr() -> dict[str, str]:
    return _parse_pairs(os.getenv("SPECIALTY_SDR"))


def resolve_sdr(account: dict, *, ids: dict[str, str] | None = None,
                by_specialty: dict[str, str] | None = None) -> str | None:
    """SDR reference for Warm/Some accounts, or None if we can't name one.

    Same logic as `resolve_ae` but reads SPECIALTY_SDR + SDR_SLACK_IDS."""
    ids = sdr_slack_ids() if ids is None else ids   # {} = deliberately no pings (test mode)
    by_specialty = by_specialty if by_specialty is not None else specialty_sdr()
    fw_key = account.get("framework_key") or account.get("framework") or ""
    # framework SDR → DEFAULT_SDR catch-all, so an unscored Warm account still tags someone.
    name = by_specialty.get(fw_key) or _env_name("DEFAULT_SDR")
    if not name:
        return None
    sid = ids.get(name)
    return f"<@{sid}>" if sid else f"@{name}"


def _env_name(var: str) -> str | None:
    """A single name from an env var (DEFAULT_AE / DEFAULT_SDR), or None if unset."""
    v = (os.getenv(var) or "").strip()
    return v or None


def live_routing() -> bool:
    """When ON, activation cards route to the real AE/SDR channels and @-ping the
    actual people. OFF (default) keeps every card on SLACK_ENGAGEMENT_WEBHOOK (the
    private testing line) with plain-text names — so testing never disturbs a channel
    with real people in it. Flip ENGAGEMENT_LIVE_ROUTING=1 to go live."""
    return (os.getenv("ENGAGEMENT_LIVE_ROUTING") or "").strip() in ("1", "true", "True")


def channel_webhook(*, is_ae: bool) -> str | None:
    """The destination webhook for a card when live routing is ON: the AE channel for
    Hot (AE) cards, the SDR channel for Warm/Some (SDR) cards. None when not live or
    unset, so post_card falls back to SLACK_ENGAGEMENT_WEBHOOK (the testing line)."""
    if not live_routing():
        return None
    return (os.getenv("SLACK_AE_WEBHOOK") if is_ae else os.getenv("SLACK_SDR_WEBHOOK")) or None


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
