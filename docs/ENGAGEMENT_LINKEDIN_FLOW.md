# LinkedIn Engagement (TOFU Ads) — Automation & Lead Flow

**Owner:** Engineering · **Last updated:** 2026-07-05 · **Linear:** MAR2-13

Purpose: full visibility into how a LinkedIn ad reaction becomes a lead, a
Salesforce record, engagement "heat", and a Slack handoff — so anyone can
monitor it and troubleshoot a specific lead without a back-and-forth.

---

## 1. End-to-end flow

```
Someone reacts (like/celebrate/etc.) on a Magical sponsored TOFU post
      │
      ▼  (scheduled scrape — see timing)
[1] Apify · harvestapi/linkedin-post-reactions   → list of reactors per post
      │
      ▼  per reactor
[2] Dedup gate (profile id)      → already-captured people are dropped BEFORE any paid step
      │
      ▼
[3] Apollo email lookup          → find a work email (the merge key downstream)
      │
      ▼
[4] ABM match gate               → keep ONLY people whose company is on the ABM target list
      │                             (non-ABM reactors are dropped here — by design)
      ▼
[5] FullEnrich phone (optional)  → best-effort phone for the contact
      │
      ├────────────► [6] Airtable "LinkedIn <> Airtable" table   (the lead-capture record)
      │                        │
      │                        ▼  Airtable native automation (create + email-gated)
      │                   [7] Salesforce Lead   (created by the Airtable automation, not us)
      │
      ├────────────► [8] Reply.io campaign      (contact added to the nurture sequence)
      │
      └────────────► [9] Engagement heat        (a `linkedin_tofu` = 6-pt event in Postgres)
                               │
                               ▼  tier recomputed (Some/Warm/Hot)
                          [10] Slack #abm-activated-accounts   (AE/SDR handoff card)
```

| # | Tool / service | Purpose | Trigger | Data in → out |
|---|---|---|---|---|
| 1 | **Apify** `harvestapi/linkedin-post-reactions` | Scrape who reacted | Scheduled run (`run_linkedin_tofu.py`) | post share_id → list of {name, profile, company} |
| 2 | Our runner (dedup) | Skip already-captured people (no double spend) | per reactor | profile id → keep / drop |
| 3 | **Apollo** | Find a work email | per new reactor | name+company → email |
| 4 | Our runner (ABM gate) | Keep only ABM-target companies | per reactor | company → keep / drop |
| 5 | **FullEnrich** | Phone number (best effort) | per surviving lead | contact → phone |
| 6 | **Airtable** ("LinkedIn <> Airtable") | Lead capture of record | upsert (email = key) | contact → Airtable row |
| 7 | **Salesforce** | CRM Lead | Airtable automation (runs as Alykhan Jina) | Airtable row → SFDC Lead |
| 8 | **Reply.io** | Nurture sequence | add contact | contact → campaign member |
| 9 | **Postgres** (engagement store) | Engagement "heat" | record event | account → `linkedin_tofu` +6 |
| 10 | **Slack** #abm-activated-accounts | AE/SDR handoff | tier change / Hot activity | account → card |

Note: **We push to Airtable, not directly to Salesforce.** SFDC lead creation
is the customer's own Airtable automation — that's the June-2026 design. Reply.io
and the heat capture are ours.

---

## 2. Campaign setup

- **Campaign type:** LinkedIn Sponsored Content (TOFU ads). The posts we monitor
  are listed by `share_id` in `auto_search/engagement/linkedin_tofu_shares.csv`
  (each row: `share_id, category` — e.g. Ortho). Add a new ad = add a row.
- **Engagement trigger:** any **reaction** (like, celebrate, support, etc.) on
  those posts. Comments are not currently captured; reactions only.
- **Lead qualification rules (the gates, in order):**
  1. **Dedup** — a person we've already captured is skipped before any paid step.
  2. **Email found** (Apollo) — no email → not upserted (email is the Airtable/Reply.io key).
  3. **ABM match** — the reactor's company must be on the ABM target list. Reactors
     from non-ABM companies are intentionally dropped (this is the main filter).
  4. Magical's own employees are dropped.
- **Enrichment:** Apollo (email) + FullEnrich (phone, best-effort).
- **Error handling:** each reactor is processed independently — one failure is
  counted and skipped, the batch always completes. Airtable/Reply.io writes are
  best-effort (a failure is logged, doesn't lose the lead). Every run stamps a
  success timestamp used by the cost throttle.

---

## 3. Lead timeline

| Step | Expected time |
|---|---|
| User reacts on the ad | Immediate |
| Reaction captured (next scheduled scrape) | **≤ 15 min** during weekday selling hours (see cadence) |
| Email + phone enrichment | Seconds (within the same run) |
| Airtable row created | Same run (≤ ~1 min after capture) |
| Salesforce Lead created | Near-real-time on Airtable create (Airtable automation) |
| Added to Reply.io campaign | Same run |
| Engagement heat recorded + tier recomputed | Same run |
| Slack handoff (if it crosses a tier / Hot activity) | **Immediately after the run** (event-driven) |

**Cadence & cost (why timing matters for troubleshooting):**
- The scrape runs on a **15-minute** schedule, but **gated to weekday selling
  hours (≈9am–7pm ET)** — outside that window a tick is a no-op *before any
  spend*. The reactions actor re-bills a post's whole reaction list per scan
  ($2 / 1,000 reactions), so at today's volume this is ≈ **$2–3/day**.
- **This is the source of most "why didn't it notify yet?" confusion:** a
  reaction that lands at, say, 8pm ET or on the weekend is captured on the next
  in-window run, not instantly. (Revert to a simple 6-hour cadence with one env
  flag if the window causes confusion.)

---

## 4. Data storage & monitoring (where to verify a lead)

A captured engagement lands in **four** places — check them in this order to
verify a specific lead made it through:

1. **Airtable — "LinkedIn <> Airtable" table** — the lead-capture record.
   Fields: name, title, company, email, phone, LinkedIn URL, post/category,
   captured-at. **This is the monitoring table Galyna asked for.** If a lead is
   here, capture + enrichment + ABM-gate all succeeded.
2. **Salesforce** — the Lead (created by the Airtable automation). If it's in
   Airtable but not SFDC, the issue is the Airtable→SFDC automation, not us.
3. **Engagement console** (the app, Engagement tab) — the account-level view:
   heat score, tier (Some/Warm/Hot), last touch, and every touch in the timeline.
   This is the internal dashboard for statuses.
4. **Postgres** `engagement_contacts` + `engagement_events` — the raw store
   behind the console (one `linkedin_tofu` +6 event per captured reaction).

### Troubleshooting playbook (the Ivy Rehab / Berkshire question)
To answer "did account X trigger a notification, and why/why not":
1. **Is the lead in Airtable + SFDC?** If yes → capture worked. (This was true
   for both Ivy and Berkshire — the leads were created fine.)
2. **Does the console show the heat event + a tier?** Open the account; the
   timeline shows each dated touch and the running heat → tier.
3. **Did the tier cross a notify threshold?** Notifications are **tier-change**
   gated, not per-lead:
   - **Berkshire** — its reaction pushed it from Lower to **Some**, a real
     upward change → it *was* due; it just landed after that morning's run, so it
     was queued for the next one (now event-driven, so this gap is closing).
   - **Ivy Rehab** — was already **Hot since March** (well before the go-live
     cutoff), so under the "only notify on a status change after the cutoff" rule
     it was correctly held — a new lead on an already-Hot account didn't re-fire.
     (This is the exact case the new Hot-reactivation rule below changes.)

---

## 5. Notification rules (tier → who)

- **Some / Warm → SDR** channel; **Hot → AE** channel. Both currently post to
  **#abm-activated-accounts** with the right person @-mentioned. **Lower → no handoff.**
- **Gated by tier CHANGE, deduped by a ledger** — an account fires once per
  upward move; it never re-fires for the same tier. This is what stops the shared
  channel being spammed.
- **Cutoff:** only status changes on/after the go-live cutoff are handed off, so
  the already-worked backlog (Jan–Jun) was never dumped into the channel.
- **Hot reactivation (agreed 2026-07-05, being added):** a **Hot** account — old
  or new — re-fires its Slack notification whenever it gets **any new activity**
  going forward. So a like on a long-standing Hot account after go-forward will
  re-alert the AE, while accounts with no new activity stay quiet.
- **Kill switch:** the whole handoff is off unless `ENGAGEMENT_NOTIFY_ENABLED=1`,
  and capped per run, so it can never flood the channel.

---

## 6. Quick reference — env & files

| Thing | Where |
|---|---|
| Runner | `scripts/run_linkedin_tofu.py` |
| Monitored ad posts | `auto_search/engagement/linkedin_tofu_shares.csv` |
| Capture + gates + enrichment | `auto_search/engagement/linkedin_ads_runner.py` |
| Heat scoring (points per touch) | `auto_search/engagement/scoring.py` (`linkedin_tofu` = 6) |
| Airtable table | env `AIRTABLE_BASE_ID` / `AIRTABLE_LINKEDIN_TABLE` |
| Cadence / cost | `LINKEDIN_TOFU_ACTIVE_HOURS_UTC`, `LINKEDIN_TOFU_WEEKDAYS_ONLY`, `LINKEDIN_TOFU_MIN_INTERVAL_HOURS` |
| Notify handoff | `scripts/run_engagement_notify.py` + `POST /api/engagement/notify-changes` |
