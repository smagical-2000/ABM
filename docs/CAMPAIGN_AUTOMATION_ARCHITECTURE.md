# Campaign Automation — Phase 3 Architecture & Scope

> The canonical reference for the outbound campaign-automation subsystem. Read this end to
> end first, then `AGENTS.md` for how we build. Companion to `ENGAGEMENT_ARCHITECTURE.md`
> (Phase 2) — Phase 3 is the send side of the same loop Phase 2 captures.
>
> **Status:** Iteration 1 BUILT (2026-07-05): §10's P1–P3 shipped on `feat/campaign-automation`
> — the enrollment engine (`auto_search/campaigns/`), the ledger (`campaign_repository.py` +
> `campaign_schema.sql`), the API (`/api/campaigns/*`), the auto-enroll-after-sync hook, and
> the Campaigns tab (`web/discovery/campaigns.jsx`). Ships dark: Testing mode + auto-enroll
> OFF by default; nothing can send until the Reply.io sequences are authored (UI-only) and
> mapped in the tab, and Live is switched on. P4 (persona/sequence-type routing) and P5
> (HeyReach LinkedIn) remain. Iteration 1 = **email only** (Reply.io).
> **Owner:** Sunny Dsouza · **Primary user:** Galyna · **Linear:** project "ABM Campaign
> Automation" (team MAR2), tickets MAR2-4/5/6/7/8.

---

## 0. Plain-language summary (for Galyna)

Phase 1 finds and scores accounts. Phase 2 watches who engages and rolls it into a heat
score. **Phase 3 closes the loop: it automatically puts the right accounts into the right
email sequence, without anyone doing it by hand.**

The bulk contacts (~8,500, one list per vertical) already live in **Reply.io**, which also
does all the actual sending (drip timing, throttling, mailbox rotation). Our platform is the
**brain**: it looks at which accounts are scored and heating up, and tells Reply.io "start
these people in this sequence." Reply.io takes it from there; Phase 2 then captures the
opens/replies/meetings and feeds heat back into the score. Nothing about the mailbox math
changes here — that is Reply.io's job; the app just decides who and when.

**What we locked (2026-07-05):**
- **The app is the orchestration brain**, not a new sending tool. Reply.io keeps the contacts
  and does the sending. (Answers: "app's role".)
- **Contacts stay in Reply.io.** The app enrolls *by account* — it already knows which Reply.io
  contacts belong to which account (Phase 2 matches them by email domain / company name).
- **Auto-enroll by rule from day one.** When an account is scored *and* prioritized, its contacts
  are enrolled automatically (seed the current backlog, then continuously as new accounts qualify).
- **Enroll all matched contacts** at a prioritized account (Reply.io skips anyone already in a
  sequence). Trimming (e.g. removing Health-System directors) happens upstream in the Reply.io list.
- **Email first (Reply.io); LinkedIn (HeyReach) is the fast-follow**, not iteration 1.

**What still needs a human decision** (see §9) — the exact "prioritized" threshold, which
sequence fires first per vertical, and the persona routing for Ortho/BH.

**One hard limit to know:** the sequences themselves (the 3 email steps + copy from the
"Sunny Email Campaign Info" doc) must be **built in the Reply.io UI** — there is no API to author
them. The app enrolls into campaigns; it cannot create the emails inside them.

---

## 1. Where this sits in the platform

```
Phase 1  Discovery ──▶ Scoring ──▶ (fit score + intent Hot/Watch)
                                      │
Phase 2  Engagement ◀── Reply.io / SFDC / podcast / LinkedIn-ads (READ) ──▶ heat tier
             │  cross to account (domain/name) · feeds intent via outcome_adjustment()
             ▼
Phase 3  Campaign Automation  ── the WRITE side ──────────────────────────────────┐
             account is scored + prioritized  ──▶  enroll its matched Reply.io      │
             contacts into the ICP sequence (add_to_campaign)  ──▶  Reply.io sends  │
             ──▶ Phase 2 captures the resulting opens/replies/meetings ─────────────┘
                 (the loop closes; heat rises; the score rises)
```

Phase 3 is a **new module that shares no tables** with discovery/scoring/engagement and holds
no foreign keys into them — same decoupling rule as Phase 2. It references accounts by the same
**soft text `account_id`** used everywhere else, so it can never lock or break the live tables.

---

## 2. The one real gap, and how we fill it without a new contact store

The app has no contact universe of its own — but it does not need one. Phase 2 already syncs
Reply.io's contact roster and stamps each contact with its matched `account_id`
(`engagement_contacts`, crossed by `engagement/cross.py`). So the app **already knows which
Reply.io contacts belong to which account.** Phase 3 reads that mapping and enrolls.

The only new addition is a small **enrollment ledger** (§4) for dedup + audit + the UI — not a
copy of the contacts.

---

## 3. The enrollment engine — generalize what already runs

`engagement/linkedin_ads_runner.py` is already a working enrollment engine (gate → resolve
contact → `add_to_campaign` → record heat, with `dry_run`, durable dedup, 409 handling, and a
Slack heads-up). Phase 3 lifts that pattern into a general, account-driven enroller.

**New: `auto_search/campaigns/enroll.py`** (pure decision) + **`campaigns/runner.py`** (the I/O loop).

Per eligible account:
1. **Resolve contacts** — `engagement_repo.contacts_for_account(account_id)` (new repo method,
   both impls). These are the account's Reply.io contacts, already matched in Phase 2.
2. **Drop the un-sendable** — `opted_out` contacts, and accounts that are `is_customer` /
   suppressed (§6).
3. **Pick the sequence** — ICP/segment → Reply.io campaign id via the mapping (§5). Persona/type
   routing is layered later (§9).
4. **Enroll** — `replyio_client.add_to_campaign(campaign_id, email, …)`. A `409` means already
   sequenced → record as `skipped_409`, never an error.
5. **Record** — one `campaign_enrollments` row per (account, contact, campaign) for dedup + audit.
6. **Notify (optional)** — reuse `engagement/notify.py` for a Slack line when an account is enrolled.

**`dry_run=True` does everything except the Reply.io write and the ledger insert** — same safety
valve as the LinkedIn runner, so a full run can be watched producing would-be enrollments with
nothing sent.

---

## 4. Data model — one new table (decoupled, idempotent)

`auto_search/db/campaign_schema.sql` (new; `CREATE ... IF NOT EXISTS`, runs on boot):

```sql
CREATE TABLE IF NOT EXISTS campaign_enrollments (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id   TEXT NOT NULL,             -- soft ref (acc_/abm_/csv_ key), like engagement
    contact_ext  TEXT NOT NULL,             -- engagement_contacts.external_id (the Reply.io contact)
    channel      TEXT NOT NULL DEFAULT 'email',  -- 'email' | 'linkedin' (later)
    sequence_key TEXT NOT NULL,             -- 'health_system:content', 'ortho:cfo', ...
    campaign_id  TEXT NOT NULL,             -- the Reply.io campaign id enrolled into
    status       TEXT NOT NULL,             -- 'enrolled' | 'skipped_409' | 'failed' | 'dry_run'
    enrolled_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    detail       JSONB NOT NULL DEFAULT '{}',
    CONSTRAINT uq_enrollment UNIQUE (account_id, contact_ext, campaign_id)
);
CREATE INDEX IF NOT EXISTS idx_enroll_account ON campaign_enrollments (account_id);
```

Reuse `engagement_settings` (key/value) for the live/testing toggle + the auto-enroll on/off flag
— no new settings table.

Repo methods (add to the `DiscoveryRepository`/engagement protocol **and both impls**):
`contacts_for_account(account_id)`, `add_enrollment(row)`, `is_enrolled(account_id, contact_ext,
campaign_id)`, `enrollments(account_id=None)`.

---

## 5. Sequence catalog & mapping (from the "Sunny Email Campaign Info" doc)

Each vertical's sequences, per the Notion doc. Every sequence = **3 email steps**. These must be
**authored in the Reply.io UI**; the app maps ICP → campaign id and enrolls.

| ICP / segment | Content sequence | Podcast (FTLORC) | Event | Persona split? |
|---|---|---|---|---|
| Health Systems (`hs`) | Article (3) | FTLORC (3) | Becker's IT+RCM (3) | no |
| Ortho (`specialty`) | Article (3) | shared FTLORC | Becker's Spine (3) | **CEO / COO / CFO** |
| Behavioral Health (`specialty`) | Video (3) | shared FTLORC | BHT (3) | **CEO / COO / CFO** |
| Radiology (`specialty`) | Article (3) | shared FTLORC | — | no |
| Payer (`payer`) | (blog library) | — | Becker's Payer (3) | no |
| Anesthesia / Derm / Cardio / Urology / Ophtho / Neuro / Pain (`specialty`) | blog library → to build | shared FTLORC | — | no |
| Cross-ICP | — | **Overall FTLORC (Seq #2)** | — | — |

**Mapping config** — extend the existing shape in `engagement/linkedin_ads.py`
(`CATEGORY_TO_CAMPAIGN`) into a Phase-3 map in `campaigns/catalog.py`:

```python
SEQUENCES = {
    "health_system": {"content": <id>, "podcast": <id>, "event": <id>},
    "ortho":         {"content": {"ceo": <id>, "coo": <id>, "cfo": <id>}, "event": <id>},
    "behavioral":    {"content": {"ceo": <id>, "coo": <id>, "cfo": <id>}, "event": <id>},
    "radiology":     {"content": <id>},
    "payer":         {"event": <id>},
    # ... campaign ids filled in once the sequences are built in Reply.io
}
```

**Iteration-1 recommendation:** route to **one primary content sequence per ICP** (the article/
video one) — the simplest, and the exact shape today's `CATEGORY_TO_CAMPAIGN` already uses. Then
add Podcast + Event sequences, then persona routing (contact title → CEO/COO/CFO) for Ortho/BH.
The full catalog above is documented so the map is ready; §9 asks Galyna which fires first.

> **Reply.io campaigns to CREATE (UI):** the outbound `content/podcast/event` sequences above do
> not exist yet. The `Engagement <ICP>` campaigns (ids 1709709–1709714) are the LinkedIn-TOFU
> nurture ones — a different purpose. New Phase-3 campaigns get their own ids, dropped into the map.

---

## 6. The trigger — auto-enroll by rule (locked: "auto from day one")

Enrollment runs from the same cron/sync loop as auto-score and auto-route (`autoscore.py`
pattern), gated + flag-controlled so it can run unattended.

**Eligible = an account that is scored AND prioritized AND sendable:**
- **scored** — on the scored/ABM list, with a fit tier (a real fit, not junk).
- **prioritized** — crosses the priority bar. Reuses signals the platform already has: fit tier
  (High/Med), intent Hot (`priority.intent`), engagement heat ≥ Warm (`engagement/scoring`).
  The exact combination is **tunable** and is the main §9 question. Conservative default:
  *fit High/Med **and** (intent Hot **or** heat ≥ Warm)*.
- **sendable** — not `is_customer`, not suppressed, has ≥1 non-opted-out matched contact, and not
  already enrolled in this sequence (ledger + Reply.io 409).

**Seed then trigger:** the first run enrolls every account that already qualifies (the backlog);
every subsequent run picks up newly-qualifying accounts. Same engine, run once vs. continuously.

**Safety rails (all reuse Phase 2 patterns):**
- **Auto-enroll toggle** (`engagement_settings`) — off by default; flip on when trusted.
- **Live / Testing toggle** — Testing does a `dry_run` (no Reply.io write), so the board can be
  explored safely; Live actually enrolls.
- **Per-run cap** — bound enrollments per run so a first "seed" can't dump thousands into Reply.io
  at once (matches the mailbox pool's real daily capacity, ~15/box/day).
- **Idempotent** — the ledger unique constraint + 409 make re-runs safe.

---

## 7. Channels — email now, LinkedIn (HeyReach) next

- **Iteration 1: Email (Reply.io).** The engine + `add_to_campaign` already exist; this is ~90%
  wiring + the ledger + the mapping + the trigger loop + a console.
- **Fast-follow: LinkedIn (HeyReach).** Net-new integration (no code today). Same engine shape —
  `channel='linkedin'`, a `heyreach_client.add_to_list/campaign`, LinkedIn's ~200 connects/week/seat
  cap enforced by HeyReach (see MAR2-6). The `campaign_enrollments.channel` column is already there
  for it. HeyReach caps + seats are budgeted separately (MAR2-6), not computed at runtime.

---

## 8. The surface — API + a Campaigns tab

- `POST /api/campaigns/enroll` — manually enroll one account (the human override / demo path).
- `GET  /api/campaigns` — the enrollment board: accounts enrolled, which sequence, status, when.
- The **auto rule** runs inside the existing sync/cron loop (like auto-score / auto-route).
- **New Campaigns tab** — `web/discovery/campaigns.jsx`, wired into `app.jsx` (nav) + `index.html`
  + `api.js`, mirroring the `engagement` tab. Shows per-account enrollment status, sequence,
  contacts enrolled, and the live/testing + auto-enroll toggles. Reuses the app's badge components.

---

## 9. Open questions for Galyna (do not block the engine build)

| # | Question | Default if unanswered |
|---|---|---|
| Q1 | **Prioritized threshold** — which fit tiers + intent/heat auto-enroll an account? | Fit High/Med AND (intent Hot OR heat ≥ Warm) |
| Q2 | **Which sequence fires first** per ICP in iteration 1 — content, podcast, or event? | Content/article sequence |
| Q3 | **Persona routing** — map contact title → CEO/COO/CFO for Ortho/BH, or one sequence for all? | One sequence for all in it.1; persona in it.2 |
| Q4 | **Suppression** — exclude active customers / open opportunities / recently-contacted? | Exclude `is_customer` + suppressed only |
| Q5 | **Re-enroll policy** — if an account goes cold then re-heats, re-enroll into a re-engagement sequence? | No re-enroll in it.1 (409 holds) |

---

## 10. Build order (right-sized; email first)

1. **P1 — enrollment engine (email).** `campaigns/enroll.py` (pure) + `campaigns/runner.py` (I/O),
   `campaign_schema.sql` + repo methods, the ICP→campaign map, `dry_run`, dedup ledger, the manual
   `POST /api/campaigns/enroll`. Lift from `linkedin_ads_runner`. Ships behind the auto-enroll flag OFF.
2. **P2 — the trigger loop + guardrails.** Wire the eligibility rule into the sync/cron loop; live/
   testing toggle; per-run cap; Slack notify.
3. **P3 — the Campaigns tab.** Enrollment board + toggles, mirroring `engagement.jsx`.
4. **P4 — sequence-type + persona routing** (podcast/event; title → CEO/COO/CFO).
5. **P5 — LinkedIn (HeyReach)** as the second channel.

---

## 11. Verification (each piece ships with its verifier — run before "done")

- **Unit** (`tests/test_campaigns.py`): the pure enroller picks the right campaign per ICP/persona;
  eligibility rule includes/excludes the right accounts; opted-out/customer/suppressed dropped;
  ledger dedup + 409 handled.
- **Integration:** seed a scored+heated account with matched Reply.io contacts → run enroll
  (`dry_run`) → ledger shows the would-be enrollments into the right campaign; a second run adds none.
- **UI smoke** (`tests/ui/test_ui_smoke.py`): the Campaigns tab renders the enrollment board.
- **Live (gated):** with the flag on + one test contact, a real `add_to_campaign` lands the contact
  in a throwaway Reply.io campaign; Phase 2 then captures the resulting activity — proving the loop.

---

## 12. Files this touches

- **New:** `auto_search/campaigns/{__init__,enroll,runner,catalog}.py`;
  `auto_search/db/campaign_schema.sql`; `web/discovery/campaigns.jsx`; `tests/test_campaigns.py`.
- **Modified:** `auto_search/db/{repository.py,postgres_repository.py,engagement_repository.py}`
  (contacts-by-account + enrollment methods); `auto_search/api/app.py` (campaign endpoints + the
  auto-enroll loop); `web/discovery/{app.jsx,api.js,index.html}`.
- **Reused as-is:** `engagement/replyio_client.py` (`add_to_campaign`), `engagement/cross.py`,
  `engagement/notify.py`, `engagement_settings`, `priority.py`, `engagement/scoring.py`.

## 13. What is blocked on a human

- **Reply.io:** the outbound sequences (§5) authored in the UI (steps + copy + mailboxes + schedule),
  then their campaign ids handed to us for the map. **API cannot do this.**
- **Galyna:** the §9 decisions (threshold, first sequence, persona, suppression).
- **HeyReach (P5):** seats + which LinkedIn accounts (MAR2-6) before the LinkedIn channel is built.
