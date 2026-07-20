# Engagement Intelligence — Phase 2 Architecture & Build Spec

> The canonical reference for the engagement subsystem. If you are an agent here to build
> a connector, read this end to end first, then `AGENTS.md` for how we build.
> Status: **M1 foundation not yet built.** M0 (access + field mapping) is blocked on the
> human providing SFDC access + the Airtable PAT. The build is designed so the **base + the
> first data load run on data we already have** (the tofu/bofu sheet) without waiting on access.

---

## 1. Why this exists

Phase 1 gives every account a **Fit** score (deep AI research) and an **Intent** score
(deterministic buying signals). Phase 2 adds the third lens marketers actually live in:
**Engagement** — did this account open emails, attend the webinar, hear the podcast, book a
meeting, move from TOFU to BOFU. We capture engagement from every channel, score it into a
**heat tier**, **cross it to the scored accounts** we already track, and **feed it back into
Intent** so an engaged account naturally rises. One pipeline, now three lenses: Fit x Intent x Heat.

The end surface: an **Engaged accounts** list ranked by heat, with the account's owner, TOFU/BOFU
stage, score, heat tier, and recent touches.

---

## 2. The data model  — `auto_search/engagement/models.py`

Two dataclasses (plain, no ORM — mirrors how `auto_search/models.py` is done today).

### `EngagementEvent` — one touch, from any channel
| field | type | notes |
|---|---|---|
| `external_id` | str | the source's own id for this touch. **Idempotency key** — re-ingesting must not duplicate. |
| `source` | str | `airtable` \| `sfdc` \| `podcast` \| `replyio` \| `manual` — which system it came from. |
| `channel` | str | `email` \| `linkedin` \| `landing_page` \| `podcast` \| `event` \| `meeting` \| `mail` ... the *kind of touch*. |
| `kind` | str | finer label inside a channel (e.g. `email_open`, `email_reply`, `bofu_form`, `meeting_held`). |
| `points` | int | what the scorer assigned (see §4). Stored so the total is auditable. |
| `company` | str | raw company name from the source (pre-match). |
| `contact` | str / null | person email or name, if the source has it. |
| `campaign` | str / null | campaign / sequence name, if any. |
| `occurred_at` | datetime | when the touch happened (drives decay, §M5). |
| `account_id` | str / null | the matched scored-account id. **Null until crossed** (§5). |
| `raw` | dict | the untouched source row, for debugging + re-mapping. |

### `EngagedAccount` — the rolled-up view of one account
| field | notes |
|---|---|
| `account_id`, `company`, `domain` | identity. |
| `owner` | the rep who owns it (from the sheet / SFDC). |
| `stage` | **TOFU / BOFU** funnel stage. |
| `score`, `heat_tier` | the engagement total + bucket (§4). |
| `meeting_booked`, `meeting_held`, `qualified` | the money milestones, surfaced explicitly. |
| `recent_touches` | last N events for the row's expander. |

---

## 3. Storage  — `auto_search/db/`

- `schema.sql`: add `engagement_events` (one row per `EngagementEvent`; unique on
  `(source, external_id)` for idempotency). All `CREATE ... IF NOT EXISTS`; it runs on API boot.
  `EngagedAccount` is **derived** (rolled up from events at read time) — do not store a second
  source of truth unless a perf need forces it.
- Add to the `DiscoveryRepository` protocol **and both impls** (`repository.py` JSON,
  `postgres_repository.py`): `add_engagement_event(event)`, `engagement_for_account(account_id)`,
  `engaged_accounts()`. Same signature, same behaviour in both — tests run on JSON, prod on PG.

---

## 4. The scorer  — `auto_search/engagement/scoring.py`  (PURE — the heart of it)

Mirror `priority.py`: no I/O, no DB, just `events -> (total, tier)`. This is the single place
weights live, so it stays auditable and testable.

**Channel/kind -> points** (from the locked spec + the tofu/bofu sheet):

| touch | points |
|---|---|
| BOFU action (demo request, pricing, bottom-funnel form) | 10 |
| Meeting agreed / booked | 10 |
| TOFU action (content download, webinar signup) | 6 |
| Podcast listen · event attend · direct mail | 4 |
| Landing-page visit · LinkedIn engagement | 2 |
| Email open / click | 1 |

**Heat tiers** (sum across the account's events):

| tier | range |
|---|---|
| Lower | 0–5 |
| Some | 6–11 |
| Warm | 12–20 |
| Hot | 21+ |

The weights table is the **single extension point** — a new channel adds one row, nothing else.

---

## 5. Cross to scored accounts  — reuse, don't reinvent

Match each event/engager to an existing scored account, in tiers:
1. **email domain** -> `scored_accounts.domain` (strongest).
2. else **company name** -> `normalize_company_name` (`auto_search/normalize.py`) -> the
   `auto_search/abm` matcher against scored-account names.
3. **no match** -> the event persists with `account_id = null` and lands in an
   **unresolved-events review queue** (M3) — never dropped, never guessed.

On match, stamp `account_id` on the event; the account now appears in the Engaged list.

---

## 6. Close the loop  — `auto_search/priority.py::outcome_adjustment()`

`outcome_adjustment()` is a **hook that already exists** in `priority.py`, provisioned for
exactly this. Feed the engagement total in so an engaged account's **Intent** score rises.
This is the payoff: engagement is not a separate silo, it lifts the same ranking the operator
already trusts. Keep the adjustment bounded + explainable (it shows up in the intent reasons).

---

## 7. The surface  — API + UI

- `GET /api/engagement` in `auto_search/api/app.py`: engaged accounts (name, domain, owner,
  TOFU/BOFU, score, heat tier, recent touches), ranked by heat.
- A new **Engagement** tab: `web/discovery/engagement.jsx`, wired into `app.jsx` (nav) +
  `index.html` (script tag) + `api.js` (the fetch). **Mirror the existing `news` tab** — it is
  the closest precedent. Heat pills reuse the app's existing badge components (the design system),
  no new visual language.

---

## 8. The connectors  — every one follows the SAME contract

A connector is just: **pull from source -> map each row to an `EngagementEvent` (with a stable
`external_id`) -> hand to the scorer + cross step.** That is the whole interface. Build them one
at a time; each is a "light-to-medium" job, not a rebuild.

| connector | status | file | what it pulls |
|---|---|---|---|
| **tofu/bofu sheet** (= the Airtable export) | **build first, no access needed** | `engagement/ingest.py` | the CSV/xlsx the user already shared -> `EngagedAccount`s + per-channel events. Reuse `auto_search/abm` `parse_workbook` / `auto_search/scoring/imports`. |
| **Airtable** | active source (needs PAT) | `engagement/airtable.py` | REST API (PAT + Base ID + table) — the live version of the sheet above. |
| **SFDC** | active source (needs API access) | `engagement/sfdc.py` | REST/SOQL via a connected app (or a scheduled report export to start) over Accounts, Contacts, Campaign members, Activities (Tasks/Events = emails + meetings), Opportunities -> events + meeting booked/held + the account. |
| **Podcast** | a source (needs the source location) | `engagement/podcast.py` | listens/attendance -> `channel=podcast` events (4 pts). Source TBD — may reuse the social Apify flow. |
| **Reply.io** | **PARKED** — do not build yet | `engagement/replyio.py` (later) | activates in the **outbound** phase as the send + capture channel. No API access yet, and rotating the leaked key is a prerequisite. |

> Reply.io note: a master key was once pasted into chat and is considered **compromised** — it
> must be rotated before use, and the new key goes only in `.env` as `REPLYIO_API_KEY` (read via
> `os.getenv`). Never commit it.

---

## 9. Build order  (right-sized — base first, then one connector at a time)

1. **M1 — the base**, on data we already have:
   a. model + storage (§2, §3) · b. the pure scorer (§4) · c. ingest the tofu/bofu sheet (§8 row 1)
   · d. **validate the scorer against the sheet** (§11) · e. cross to scored (§5) · f. API + tab (§7)
   · g. close the loop (§6).
2. **M2 — live connectors** when access lands: Airtable, then SFDC. (Reply.io parked.)
3. **M3 — identity**: the unresolved-events review queue + match tiers.
4. **M4 — heat & activation**: the heat list + alerts on tier changes.
5. **M5 — decay & guardrails**: an engagement TTL/decay clock (recent touches weigh more) +
   `spend_guard` on any SFDC/AI enrichment.

Do **a** through **g** before any live pull. The validation in (d) is the gate that proves the
weights + thresholds are right on real numbers before we trust a live pipe.

---

## 10. Verification  (each piece ships with its verifier — run before "done")

- **Unit** (`tests/test_engagement.py`): the scorer reproduces the sheet's **Total Score + Intent
  Level** on sample rows; channel->points; domain/company -> scored-account match.
- **Integration**: ingest the sheet -> engaged accounts persist with score + heat; cross a seeded
  scored account; `GET /api/engagement` returns it ranked.
- **UI smoke** (`tests/ui/test_ui_smoke.py`): the Engagement tab renders engaged accounts with
  heat pills, seeded via the local DB (same pattern as the news/intent regressions).
- **Live**: once Airtable/SFDC access lands, a sync pulls real rows -> the tab updates -> deploy.

---

## 11. Reuse map  (the toolbox — check here before writing anything new)

| need | use |
|---|---|
| parse the sheet / workbook | `auto_search/abm` `parse_workbook`, `auto_search/scoring/imports` |
| normalize a company name | `auto_search/normalize.py` |
| match a company -> scored account | the `auto_search/abm` matcher |
| the accounts to cross against | scoring repo `list_scored()` / `.domain` |
| the closed-loop slot | `priority.outcome_adjustment()` |
| pure-scorer shape to copy | `auto_search/priority.py` |
| guard any paid enrichment | `auto_search/scoring/spend_guard.py` |
| the Engagement tab pattern | the `news` tab (`web/discovery/news.jsx` + its wiring) |
| heat pills | the app's existing badge components |

---

## 12. Files this touches

- **New:** `auto_search/engagement/{__init__,models,scoring,ingest}.py` (+ `airtable.py`,
  `sfdc.py`, `podcast.py` as each connector lands); `web/discovery/engagement.jsx`;
  `tests/test_engagement.py`.
- **Modified:** `auto_search/db/{schema.sql,repository.py,postgres_repository.py}`;
  `auto_search/api/app.py` (engagement endpoints); `auto_search/priority.py`
  (`outcome_adjustment`); `web/discovery/{app.jsx,api.js,index.html}`; `.env` (the live keys).

---

## 13. Linear  — the task decomposition

Project **"ABM Engagement Intelligence"** (team AGT). Milestones map 1:1 to §9:
M0 access & mapping (blocked on the human) · M1 foundation · M2 connectors · M3 identity ·
M4 heat & activation · M5 decay & guardrails. Each ticket's acceptance criteria = its verifier
in §10. Build a connector on its own `feat/engagement-<name>` branch -> small PR -> CI -> review.

## 14. What is blocked on the human (M0)

- **Airtable:** a Personal Access Token + Base ID + table name(s); confirm the shared CSV = that table.
- **SFDC:** API access (connected-app client id/secret + a user) OR a report/export, and which
  objects/fields hold TOFU/BOFU, touches, meeting booked/held, qualified, owner, account name/domain.
- **Podcast:** where the listen/attendance data lives.
- **Reply.io:** parked — a rotated key, only when the outbound phase starts.
