# Engagement Intelligence — Reply.io · PRD (v1.1)

Status: **approved** (2026-06-14). Built per the MyZone agentic field guide
(requirements → architecture → build → QA-per-milestone → review → deploy).

## Goal
Pull real **email engagement from Reply.io** (read-only), attribute it to accounts
we already track (**scored accounts + the ABM target list**), score it into **heat**,
and surface it in the **Engagement console** — so the team sees which tracked
accounts are actually engaging, with real numbers.

## Primary user
The ABM operator/analyst (same persona as Discovery / Scored), now with a third
lens: **Heat** (alongside Fit and Intent).

## Scope
**In:** Reply.io v3 (read-only) email engagement → Postgres → matched to scored +
ABM → scored into heat → Engagement console (Accounts list, Inbox feed, account
drawer).
**Out / deferred:** the tofu/bofu sheet + non-email channels, LinkedIn activity,
SFDC/podcast, AI identity resolution, Log-touch, Activate-to-SDR (Slack),
decay/TTL, any write-back to Reply.io.

## Data source — Reply.io v3, read-only (`Authorization: Bearer`, 100/min)
- Contacts: `GET /v3/contacts` → `email, domain, company, title, meetingStatus, isOptedOut`.
- Email engagement: `POST /v3/reporting/emails` (report query; date filter; paginated)
  → per contact-send `isDelivered/isOpened/isClicked/isReplied/isInterested/isBounced/isOptedOut`.
- Timeline (drawer, lazy): `GET /v3/contacts/{id}/activities`.
- **Window:** last **30 days** (auto-extend to 60 if sparse), configurable.
- **Cadence:** on-demand sync first; scheduled cron later.

## Scoring (canonical "Account Scoring Rules" CSV)
Tiers: **Lower 0–5 · Some 6–11 · Warm 12–20 · Hot 21+**.
Reply.io → points mapping (email subset):

| Reply.io signal | Points |
|---|---|
| click (`isClicked`) | 1 |
| reply (`isReplied`) | 6 |
| meeting booked (`meetingStatus`) | 10 |
| open (`isOpened`) | 0 (Apple-MPP noise) |
| `isInterested` | flag, not points |
| bounce / opt-out | excluded + suppressed |

**Account heat = Σ points over its contacts**, each contact counting click/reply/
meeting **at most once** (enforced structurally by the event primary key, so a long
contact list can't inflate). Email-only means many accounts read Lower/Some until
other channels land — the honest first picture.

## Matching to accounts
- Tier 1 — **domain**: contact domain → `scored_accounts.domain`, then ABM-target domain.
- Tier 2 — **company name**: `normalize_company_name` → scored/ABM keys.
- Personal domains (gmail/yahoo): company-name only; else **unresolved review list** (no AI).
- A company that is both scored + ABM → **one merged row, tagged both**.

## UI (Visual Blueprint = the DS engagement console, ported in as a 5th tab)
- **Accounts:** ranked by heat — owner, heat+score, score bar, channel mix, last
  touch, open-rate / reply-rate.
- **Inbox:** per-contact engagement feed; **Resolve** = a plain review list.
- **Drawer:** score breakdown, contacts engaging, timeline.
- **Stubbed (visible, inert):** Log touch, Activate-to-SDR.

## Non-functional
Read-only Reply.io · key via `os.getenv` (`.env`, gitignored) · PII stored only in
our PG · rate-limit backoff · idempotent re-sync · modular + documented for review ·
**nothing deploys to prod without explicit approval**.

## Acceptance / verifiers (inner loop, one per milestone)
DB applies + documented → client unit-tested (mocked, pagination/backoff) → ingest
idempotent (re-run = 0 dupes) → crossing tested vs seeded scored+ABM → pure scorer
tests reproduce the mapping + tiers → `GET /api/engagement` via TestClient →
Playwright smoke (Accounts+Inbox+drawer, zero console errors) + visual diff vs the
blueprint. Every bug → `evals/bugs.json`.

## Milestones (each = small PR + QA gate + human review)
A DB module · B Reply.io client · C ingest (raw→normalized) · D crossing · E scorer
· F API · G UI.

## Decisions log
- 2026-06-14: Reply.io only first; new CSV is canonical; documented idempotent
  schema (no Alembic); full read UI with Resolve=list + Log/Activate stubbed;
  merged scored+ABM row; 30→60-day window; reply=6 / meeting=10 / open=0.
