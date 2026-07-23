# RULES.md — the scoring & notification rules ledger

**Contract:** every scoring/gating rule the platform enforces lives HERE, with
its owner, date, and where it's enforced. A rule agreed in Slack/meeting gets a
same-day entry + code commit (or an explicit ticket) — "agreed but never
shipped" (the Intro-only meeting rule, the compound-BOFU definition, the Hot
reactivation clause) is the failure mode this file exists to kill. If code and
this file disagree, that's a bug in one of them — say so loudly.

## Heat matrix (points per touch kind)

Canonical values live in `auto_search/engagement/scoring.py::POINTS` (one row
per kind — that file is the source of truth; this table is the human index).
Meeting booked / BOFU / opportunity / tradeshow-qualified / messaged-connect
accept = 10 · replies + TOFU (form or LinkedIn ad) = 6 · podcast = 4 · bare
connect = 2 · clicks = 1. Tiers: **Hot ≥ 21 · Warm ≥ 12 · Some ≥ 6**.

## Gates & caps

| Rule | Owner / date | Enforced in |
|---|---|---|
| **Click cap 3** — click-kind points (`click`, `outbound_click`, `email_click`) count at most **3 per account**; scanner bursts score like 3 real clicks (AGT-1453) | Sunny · 2026-07-20 | `scoring.capped_score` + `engaged_accounts` view + JSON rollup + `scores_before` + audit recompute + drawer journey strip |
| **Intro-only meetings** — `meeting_booked` (+10) counts only SFDC meetings whose subject contains *Intro/Introduction/Introductory*; demos & follow-ups are pipeline motion, not a new-meeting signal | Griffen def · ratified Sunny 2026-07-20 | `sfdc.parse` meetings loop |
| **High-intent definition** — LeadSource in the org set **or any `… \| BOFU` compound** (mirrors Griffen's High-Intent dashboard) | Griffen dashboard · aligned 2026-07-20 | `sfdc_client.iter_high_intent_leads` |
| **TOFU echo suppression** — `TOFU Engagement Campaign` leads are our own Airtable automation echoing LinkedIn captures into SFDC: they score only when the person wasn't already scored at capture; duplicate leads for one person collapse to the oldest | Sunny · 2026-07-20 | `sfdc.filter_tofu_echoes` (called by sync + reconcile) |
| **Activation cutoff 2026-06-25** — accounts whose newest real touch predates the cutoff never fire (imported history ≠ fresh activation) | Galyna · 2026-06-25 | `notify.accounts_to_notify` |
| **Hot reactivation** — an already-Hot account re-fires when its newest REAL (non-click) touch is newer than the last-notified touch (MAR2-44 #1, 2026-07-23). Scope decision: applies to NEW activity only; the 2026-07-10 baselined backlog stays silenced (working backlog Hots is a campaign decision, not a notification) | Galyna 2026-07-03 · shipped 2026-07-05 · scope Sunny 2026-07-20 · real-clock 2026-07-23 | same |
| **Trigger clock** — clicks never arm/advance activation triggers (account-level: cutoff, Hot reactivation, seed baseline, twin dedup ranking; display `last_touch` unaffected) | MAR2-44 #1 · 2026-07-23 | `last_real_touch` (view + JSON rollup) + `notify.trigger_touch` (gates/dedup/seed) + `/activate` cutoff clock |
| **Activation channel is ABM-only** — `accounts_to_notify(abm_only=True)` (membership is company-level across twins) + the `/activate` non_abm gate; the leads-ads feed is un-gated, non-ABM tagged | Sunny · 2026-07-22 | notify-changes / `/api/engagement/due` + `/activate` + audit I4 |
| **Non-ABM never activates** — only accounts on the ABM list get activation cards; a scored-only engager stays board-only, and unmatched inbound is dropped, not queued | Sunny · standing (2026-07-08) | `accounts_to_notify(abm_only=True)` + `/activate` non_abm gate (2026-07-23); unmatched inbound additionally dropped at `cross_and_persist(persist_unmatched=False)` + strict-ABM eval |
| **Tradeshow = Qualified only** — badge scans are 0 until sales marks the lead Qualified | Galyna · 2026-06 review | `iter_tradeshow_leads` |
| **Send-lists come from the evaluator** — `GET /api/engagement/due` is the ONE computation of "due"; hand-assembled lists are banned (two name-keying drifts on 2026-07-20) | Sunny/Claude · 2026-07-20 | `/api/engagement/due` |
| **Breaker ceiling 25** — abnormal due volume holds everything and alerts; `allow_burst=true` only after human review | MAR2-31 · 2026-07-09 | notify-changes endpoint |

## Schedules & ops

- **Daily cron `30 12 * * 1-5`** = 8:30 AM EDT (Sunny 2026-07-20; was 14:00 UTC).
  Railway crons are UTC-fixed — winter this reads 7:30 AM EST; adjust deliberately or accept.
- **Weekday-only is a CHOSEN blind spot** (Sunny 2026-07-20): weekend inbound
  scores Monday 8:30. Revisit only if a weekend BOFU matters commercially.
- **TOFU capture runs every 15 min** (`linkedin-tofu-cron`, `*/15 * * * *`) — near-real-time.
- **Deploys go through `scripts/ship.sh` only** — all four services, one stamp,
  parity-verified (`/api/health` build + I6-fleet heartbeats).
- **Reconcile leg** diffs 14 days of SFDC vs the store daily and alerts on
  misses — label drift is caught in 24h, never weeks.
- **Daily digest** posts every weekday run; a MISSING digest is itself the alarm.
- **Alerts**: `[QA · …]` prefix = stress-test, not an incident (set `ALERTS_QA_MODE=1`
  in test envs). Every alert carries a "What to do" runbook line.
