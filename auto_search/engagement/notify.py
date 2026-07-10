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

from auto_search.normalize import company_name_words

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


# ── TOFU low-intent lead → Slack (the "heads up before Salesforce" card) ──────
# A single enriched LinkedIn-engagement lead, posted to SLACK_TOFU_WEBHOOK right
# before it is created in Salesforce, so the team sees every TOFU lead as it lands.
# Unlike the account activation card (clean, no emoji), this one is intentionally a
# bit more visual — it's a fast scannable "new lead" ping, not a sales packet.

_SEGMENT_LABEL = {"specialty": "Specialty", "health_system": "Health System",
                  "payer": "Payer"}


def build_lead_card(lead: dict, *, test: bool = False) -> dict:
    """Slack Block Kit card for one TOFU low-intent lead. PURE.

    `lead` keys (all optional except name): name, title, company, email, phone,
    linkedin, segment (specialty/health_system/payer). Renders a green-barred card:
    person up top, a two-column contact grid, the source line, a LinkedIn button,
    and a context footer noting it's entering Salesforce."""
    name = (lead.get("name") or "New lead").strip()
    title = (lead.get("title") or "").strip()
    company = (lead.get("company") or "—").strip()
    email = (lead.get("email") or "").strip()
    phone = (lead.get("phone") or "").strip()
    linkedin = (lead.get("linkedin") or "").strip()
    segment = _SEGMENT_LABEL.get((lead.get("segment") or "").strip().lower())

    fields = [{"type": "mrkdwn", "text": f"*🏢 Company*\n{company}"}]
    if segment:
        fields.append({"type": "mrkdwn", "text": f"*🧭 Segment*\n{segment}"})
    if email:
        fields.append({"type": "mrkdwn", "text": f"*✉️ Email*\n{email}"})
    if phone:
        fields.append({"type": "mrkdwn", "text": f"*📞 Phone*\n{phone}"})

    person = f"*{name}*" + (f"\n_{title}_" if title else "")
    blocks: list[dict] = [
        {"type": "header",
         "text": {"type": "plain_text", "text": "🎯 New TOFU Lead", "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": person}},
        {"type": "section", "fields": fields[:10]},
        {"type": "section", "text": {"type": "mrkdwn",
         "text": "*📣 Source*   LinkedIn · paid-social · _TOFU Engagement Campaign_"}},
    ]
    if linkedin.startswith(("http://", "https://")):   # Slack rejects scheme-less URLs
        blocks.append({"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text",
             "text": "🔗 LinkedIn profile", "emoji": True}, "url": linkedin}]})
    foot = "🧪 Wiring test" if test else "📥 Captured from LinkedIn engagement · entering Salesforce"
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": foot}]})

    return {"text": f"New TOFU lead: {name} — {company}",
            "attachments": [{"color": "#2EB67D", "blocks": blocks}]}


def notify_lead(lead: dict, *, webhook: str | None = None, test: bool = False,
                http: httpx.Client | None = None) -> bool:
    """Build + post the TOFU lead card. Webhook defaults to SLACK_TOFU_WEBHOOK.
    Best-effort (never raises) — a Slack hiccup must not block the Airtable write.

    Resolves the hook here and no-ops when it's unset, rather than delegating to
    post_card — otherwise a missing SLACK_TOFU_WEBHOOK would fall back to
    SLACK_ENGAGEMENT_WEBHOOK and leak TOFU lead cards into the activation channel."""
    hook = webhook or os.getenv("SLACK_TOFU_WEBHOOK")
    if not hook:
        logger.warning("SLACK_TOFU_WEBHOOK not set — skipping TOFU lead post")
        return False
    return post_card(build_lead_card(lead, test=test), webhook=hook, http=http)


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
    # `framework`, then to the ABM segment for engaged-but-unscored accounts that have no
    # framework. SPECIALTY_AE is keyed by it.
    fw_key = (account.get("framework_key") or account.get("framework")
              or _framework_from_segment(account.get("segment")) or "")
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
    fw_key = (account.get("framework_key") or account.get("framework")
              or _framework_from_segment(account.get("segment")) or "")
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


def _framework_from_segment(segment: str | None) -> str | None:
    """Map an account's ABM segment to a routing framework key, so engaged-but-unscored
    accounts (no Phase-1 framework) still tag the right AE/SDR instead of the default.
    Keys match SPECIALTY_AE/SPECIALTY_SDR (health_system / specialty / payer)."""
    s = (segment or "").strip().lower()
    if not s:
        return None
    if "payer" in s:
        return "payer"
    if "special" in s:
        return "specialty"
    if "health system" in s or "hospital" in s:
        return "health_system"
    return None


def live_routing() -> bool:
    """The ENV default for live routing. The console's runtime toggle (stored in the
    repo) overrides this when set; the activate endpoint resolves the two. When live,
    cards route to the real AE/SDR channels and @-ping the actual people. OFF (default)
    keeps every card on SLACK_ENGAGEMENT_WEBHOOK (the private testing line) with
    plain-text names — so testing never disturbs a channel with real people in it."""
    return (os.getenv("ENGAGEMENT_LIVE_ROUTING") or "").strip() in ("1", "true", "True")


def tier_webhook(*, is_ae: bool) -> str | None:
    """The destination webhook for a tier: the AE channel for Hot (AE) cards, the SDR
    channel for Warm/Some (SDR) cards. The caller only uses this when live; otherwise
    it passes None so post_card falls back to SLACK_ENGAGEMENT_WEBHOOK (testing line)."""
    return (os.getenv("SLACK_AE_WEBHOOK") if is_ae else os.getenv("SLACK_SDR_WEBHOOK")) or None


# ── tier-change handoff gating (auto AE/SDR push) ─────────────────────────────
# Send ONCE per upward tier move, not on every point change: Some/Warm → SDR, Hot → AE.
# Hot is terminal (rank can't rise past it, so it never re-sends). A downward drift
# (decay/TTL) never re-sends. Callers persist the last-notified tier per account and
# pass it in as `notified`.
_TIER_RANK = {"lower": 0, "some": 1, "warm": 2, "hot": 3}


def tier_role(tier: str | None) -> str | None:
    """Owner role for a tier: Hot → 'ae', Warm/Some → 'sdr', else None (no handoff)."""
    t = (tier or "").strip().lower()
    if t == "hot":
        return "ae"
    if t in ("warm", "some"):
        return "sdr"
    return None


def _ledger_entry(v) -> tuple[str, str | None]:
    """Normalize a `notified` ledger value to (tier, last-notified-touch).
    Two shapes coexist: the new {"tier","touch"} dict, and the legacy bare tier
    string (no touch). A missing touch is None."""
    if isinstance(v, dict):
        return (v.get("tier") or "Lower"), v.get("touch")
    return (v or "Lower"), None


def company_key(name: str | None) -> str:
    """Stable NOTIFICATION identity for a company — survives internal account-id
    re-keys (2026-07-09 incident: bulk imports minted csv_/acc_ twins of abm_
    identities; the account-id-keyed ledger read 83 already-handled companies as
    brand-new tier rises).

    Composes the canonical `normalize_company_name` (never modified here — its
    output is minted into persisted account ids, so changing IT would re-key the
    board) with one matching-only extension: a leading article is dropped so
    "The Harris Center…" and "Harris Center…" share notification history.
    Degeneracy guard: the article is kept when what remains is a single word —
    suffix-stripping already collapses generic names hard ("The Urology Group"
    -> "theurology"), and stripping further would merge DISTINCT companies
    ("Urology Associates" -> "urology"). Verified against all 2,222 live company
    names: with the guard, the only merges are true same-company variants.

    This is a LOOKUP key for the notify ledger only. Never mint identities from
    it. Returns "" for a missing name — callers must fall back to account_id."""
    stripped = company_name_words(name or "")
    raw = [w for w in _NON_ALNUM_RE.sub(" ", (name or "").lower()).split() if w]
    # Degeneracy guard, generalized (QA panel 2026-07-09): when suffix-stripping
    # collapses a name to a SINGLE substantive word, distinct companies merge
    # ("Urology Group" and "Urology Associates" would both key as "urology").
    # In that case key on the UNSTRIPPED words — same-suffix variants of one
    # company still match, different-suffix companies stay distinct. Names whose
    # stripped form keeps 2+ substantive words use the normal stripped key, so
    # "Acme Health LLC" == "Acme Health Inc." is preserved.
    core = stripped[1:] if (stripped and stripped[0] == "the") else stripped
    basis = raw if (len(core) <= 1 and len(raw) > len(stripped)) else stripped
    if len(basis) >= 3 and basis[0] == "the":
        return "".join(basis[1:])
    return "".join(basis)


_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def stronger_state(a: dict, b: dict) -> dict:
    """The stronger of two ledger states: higher tier, then newer touch. The
    single collision rule shared by the migration, the seed, and the send path
    — a weaker twin can never downgrade a company's recorded state."""
    ta = _TIER_RANK.get(str(a.get("tier") or "").lower(), 0)
    tb = _TIER_RANK.get(str(b.get("tier") or "").lower(), 0)
    if ta != tb:
        return a if ta > tb else b
    da, db = _touch_dt(a.get("touch")), _touch_dt(b.get("touch"))
    if da is None:
        return b
    if db is None:
        return a
    return a if da >= db else b


def record_notified(notified: dict, account: dict, tier: str, touch) -> None:
    """Merge-strongest ledger write (QA panel 2026-07-09: plain assignment let
    a weaker board twin overwrite its company's stronger recorded state — the
    seed's 'nothing fires after seed' guarantee broke, and Hot re-fired against
    a downgraded ledger). Mutates `notified` in place."""
    key = ledger_key(account)
    entry = {"tier": tier, "touch": touch,
             "account_id": account.get("account_id"), "name": account.get("name")}
    prev = notified.get(key)
    if prev is not None:
        prev_d = prev if isinstance(prev, dict) else {"tier": str(prev), "touch": None}
        entry = stronger_state(prev_d, entry)
    notified[key] = entry


def _ledger_lookup(notified: dict, account: dict) -> tuple[str, str | None]:
    """The strongest previously-notified state for an account across EVERY key
    form its company may be recorded under: the company key (post-migration),
    the raw account_id (pre-migration / fallback writes), and the account-id
    body itself when it embeds the canonical name key (abm_<key> ids do).

    Strongest = highest tier, then newest touch. Taking the max is the
    conservative direction: identity churn can only ever SUPPRESS a duplicate
    alert, never invent one — a genuinely new rise still beats any prior state."""
    aid = account.get("account_id") or ""
    name = account.get("name")
    if name == aid:                 # display fallback, not a real name (see ledger_key)
        name = None
    ck = company_key(name)
    canonical = "".join(company_name_words(name or ""))
    body = aid.split("_", 1)[1] if "_" in aid else ""
    # Every key form this company's history may live under: the company key
    # (post-migration), the canonical un-articled key, this row's own id and
    # id-body (pre-migration self), and the MINTED abm id for the company —
    # `abm_<canonical>` is how cross.py names ABM identities, which is where a
    # re-keyed csv/acc twin's pre-migration history actually lives.
    probes = [ck, canonical, aid, body,
              f"abm_{canonical}" if canonical else "", f"abm_{ck}" if ck else ""]
    seen: set[str] = set()
    candidates = []
    for p in probes:
        if p and p not in seen:
            seen.add(p)
            if p in notified:
                candidates.append(notified[p])
    if not candidates:
        return "Lower", None
    best_tier, best_touch = "Lower", None
    for v in candidates:
        tier, touch = _ledger_entry(v)
        t_rank, b_rank = _TIER_RANK.get(tier.lower(), 0), _TIER_RANK.get(best_tier.lower(), 0)
        if t_rank > b_rank:
            best_tier, best_touch = tier, touch
        elif t_rank == b_rank:
            t_dt, b_dt = _touch_dt(touch), _touch_dt(best_touch)
            if b_dt is None or (t_dt is not None and t_dt > b_dt):
                best_touch = touch or best_touch
    return best_tier, best_touch


def ledger_key(account: dict) -> str:
    """The key NEW ledger entries are written under: the company key, falling
    back to the account_id when the account has no usable name. An id-as-name
    (the board's display fallback when a name can't be resolved) is NOT a name —
    normalizing it would mint a garbage key ('abm_dueco' -> 'abmdueco') that a
    properly-named row of the same company could never match, re-creating the
    exact re-fire bug this keying exists to kill."""
    aid = account.get("account_id") or ""
    name = account.get("name")
    if not name or name == aid:
        return aid
    return company_key(name) or aid


def _touch_dt(ts):
    """Parse an ISO touch timestamp to an aware datetime, or None. Robust to
    mixed offsets ('+00:00' vs '-04:00') and a trailing 'Z' — a plain string
    compare is wrong across offsets."""
    if not ts:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def accounts_to_notify(accounts: list[dict], notified: dict,
                       cutoff: str | None = None) -> list[dict]:
    """Accounts that should fire an AE/SDR handoff right now. PURE.
    `notified` maps ledger keys — company keys (see `company_key`) and/or
    legacy account_ids — to {"tier","touch"} (or a legacy bare tier string);
    `_ledger_lookup` resolves an account across every form its company may be
    recorded under, so internal account-id re-keys (bulk imports minting new
    identities for known companies, 2026-07-09) can never resurrect an
    already-notified company as a "new" tier rise.
    Returns [{account, tier, role, prev, reason, touch}].

    Three gates:
      - CUTOFF (Galyna 2026-06-25, enforced here since 2026-07-09): an account
        whose newest touch predates `cutoff` (YYYY-MM-DD) never fires — newly
        imported companies whose OLD history just attached must not read as
        fresh activations. No touch at all also never fires.
      - ROSE: the tier climbed above the last tier we notified (Some→Warm→Hot).
        The only path for Some/Warm — same-tier activity never re-fires.
      - HOT REACTIVATION (Galyna 2026-07-05): an account that is ALREADY Hot
        re-fires when it gets NEW activity — its latest touch is newer than the
        touch we last notified it on. Requires a recorded baseline touch, so a
        never-seeded account can't back-fire its whole history on the first run
        (missing baseline = treated as already acknowledged)."""
    # Per-company dedup FIRST (QA panel 2026-07-09, blocker): id twins of one
    # company must be gated as ONE company — otherwise both twins fire in the
    # same pass (an AE card AND an SDR card for one account). Keep the
    # strongest row per ledger key (highest tier, then newest touch).
    best: dict[str, dict] = {}
    for a in accounts:
        key = ledger_key(a)
        cur = best.get(key)
        if cur is None:
            best[key] = a
            continue
        pick = stronger_state(
            {"tier": cur.get("tier"), "touch": cur.get("last_touch"), "_row": cur},
            {"tier": a.get("tier"), "touch": a.get("last_touch"), "_row": a})
        best[key] = pick["_row"]
    out: list[dict] = []
    for a in best.values():
        tier = a.get("tier") or "Lower"
        role = tier_role(tier)
        if not role:                                   # Lower / unknown → no handoff
            continue
        touch = a.get("last_touch")
        if cutoff and (not touch or str(touch)[:10] < cutoff):
            continue                                   # stale history — never alert
        prev_tier, prev_touch = _ledger_lookup(notified, a)
        rose = _TIER_RANK.get(tier.lower(), 0) > _TIER_RANK.get(str(prev_tier).lower(), 0)
        reactivated = False
        if not rose and tier.lower() == "hot":
            cur_dt, prev_dt = _touch_dt(touch), _touch_dt(prev_touch)
            reactivated = cur_dt is not None and prev_dt is not None and cur_dt > prev_dt
        if rose or reactivated:
            out.append({"account": a, "tier": tier, "role": role, "prev": prev_tier,
                        "reason": "rose" if rose else "hot_activity",
                        "touch": touch})
    return out


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
