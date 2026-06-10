# ABM Discovery — Engineering Context & Handoff

> Audience: an engineer or AI agent picking up this codebase cold. Read top to
> bottom once; after that, the file/section map is enough to navigate.
> Last updated: 2026-06-09.

---

## 1. What this product is

**Magical** sells an agentic-AI **Revenue Cycle Management (RCM)** platform to US
**healthcare providers and payers** — it automates the manual back-office work
(prior auth, eligibility, claims, denials/appeals, billing, coding, AR).

**ABM Discovery** is the internal **account intelligence platform** that finds and
scores the accounts Magical's GTM team should target (ABM = account-based
marketing). It has two halves:

- **Discovery** — pull *buying signals* about companies (job postings, leadership
  changes, funding, acquisitions, layoffs, LinkedIn post engagement, event
  attendance), **qualify** each company for ICP fit (Claude web research), and
  drop the winners into a **review panel** where a human (Galyna) promotes or
  rejects them.
- **Scoring** — score promoted/imported accounts against ICP frameworks
  (the original V0 CLI scorer). Real cost ≈ **$0.09–0.12 per account**.

**Audience bar:** the panel is shown to the **VP of Engineering, CTO, and board
members**. That drives three hard rules that recur everywhere:
1. **UX is paramount** — if a user is confused or the UI looks unpolished, it's a
   failure. **No emojis** in the UI (monochrome typographic glyphs like `✓ ✕` are
   fine; pictographic emoji are not).
2. **No bugs** — changes get scrutinized; verify before declaring done.
3. **Cost-sensitive** — every paid call (Apify scrape, Claude qualify, enrichment)
   is shaped to avoid waste.

---

## 2. Architecture at a glance

| Layer | Tech | Notes |
|---|---|---|
| API | **FastAPI** (`auto_search/api/app.py`, ~1.2k lines) | sync handlers in a threadpool |
| Frontend | **In-browser React via Babel standalone** (`web/discovery/*.jsx`) | **NO build step** — JSX is compiled client-side. A JSX **syntax error white-screens the whole app.** Served as static files at `/` (behind Basic auth). |
| DB | **Postgres** (psycopg3, sync) | schema `auto_search/db/schema.sql`, **auto-migrated on boot** via `ensure_schema()` (all `CREATE TABLE IF NOT EXISTS`). |
| Repo abstraction | `DiscoveryRepository` protocol | Two impls: `PostgresRepository` (prod) and `JsonFileRepository` (local/tests/no-infra) — same interface, mirror each other. |
| LLM | **Claude Sonnet 4.5** (`ANTHROPIC_MODEL=claude-sonnet-4-5`) | NOT Opus. (A stale `claude-opus-*` default exists in code but `.env` overrides to Sonnet.) The company qualifier uses **web_search**. |
| Hosting | **Railway**, project `abm-discovery` (`d9170dc2…`) | Services: **discovery-api** (web), **discovery-cron** (scheduled), **Postgres**. Deploy per-service via `railway up --service <name>` (Nixpacks). |
| External data | **Apify** (Indeed + LinkedIn job scrapers; LinkedIn post-engagement + event-post-search actors), **SignalBase** (leadership/funding/acquisitions), **WARN** (layoffs — needs a browser, so cron-worker only). |

**Repos / git:** canonical remote is **`getmagical/abm-discovery`**. `origin`
(`smagical-2000/ABM`) has unrelated history — ignore it. Work flows on a feature
branch → **PR #1** → `main`. (PR #1 is now **merged**, commit `a20e47b`.)

---

## 3. The discovery pipeline (the core seam)

```
connector.pull(since)  →  [prefilter]  →  group by company_key  →
  skip_already_qualified?  →  defer (park)?  →  qualify (Claude)  →  CompanyCandidate
```

- **Connectors** (`auto_search/connectors/*.py`) yield `RawSignal` from `pull(since)`.
  Pure + LLM-free + unit-testable. Each signal carries `company_name_raw`,
  `company_key` (normalized name = dedup key), `signal_type`, `signal_strength`,
  `observed_at`, `payload`.
- **`auto_search/pipeline.py`** — `collect_unique_companies` (pull → optional
  `prefilter` → group) and `run` (the qualify loop). Two dedup layers guarantee
  **one Claude call per company, ever**: grouping (within-run) +
  `skip_already_qualified` (across runs). Connector-agnostic hooks:
  - `prefilter` — async filter over all signals before grouping (jobs uses it for
    the cheap job-level qualifier).
  - `defer` / `on_defer` — **per-company predicate** to *skip qualify without
    marking decided* (re-checked next run). This is the **jobs stacking gate**.
  - `gate` — cooperative pause/cancel checkpoint (RunControl) before each paid call.
  - `on_plan` — reports the qualify denominator for the progress %.
- **`auto_search/discovery_runner.py`** — `run_once(repo, days, sources, …)`
  orchestrates the **browserless** sources (leadership, acquisitions, funding,
  jobs) inside the **web** process so results land live in the panel. Resilient
  (one source failing doesn't kill the run). Records per-company spend
  (`on_company`) and a `connector_runs` heartbeat.
- **`auto_search/qualifier.py`** — the expensive per-company ICP qualifier (Claude
  + web_search). Returns a `QualificationResult` → `to_status()` ∈
  {qualified, needs_review, disqualified, error}.

**Storage** (`auto_search/db/repository.py` = protocol + JSON impl;
`postgres_repository.py` = Postgres impl): persists the **verdict + provenance**,
never the raw firehose. `save_candidate` upserts the company and dedups signals.
`panel()` feeds the UI. Also stores ABM targets, social targets, event keywords,
and the stacking **parked** ledger (see §5).

---

## 4. What's been built (recent work streams)

1. **Cost baseline** — confirmed ≈ **$0.09–0.12/account** from prod `cost_events`.
2. **ABM cross-reference** — the ABM target sheet (Q2 workbook) is cross-referenced
   in the panel; an **ABM target tag** marks matches. Deployed + workbook uploaded.
3. **ABM-on-scored** — the ABM tag carries to the **scored** board, but **only for
   Discovery-sourced accounts** (CSV imports are ABM-by-definition → no tag).
   Removed a redundant "Qualified" tag in discovery; scored rows use absolute
   timestamps.
4. **Social listening (Apify)** — two flows, both: qualify the **company's ICP on
   the free `position`/headline FIRST, then pay to enrich**; keep **US**
   **decision-makers** only; drop Magical's own staff.
   - *Post engagement* — likes/comments on Magical's + competitors' posts →
     `social_engagement` signal (person stored as the contact).
   - *Event attendees* — keyword post-search (`datadoping~linkedin-posts-search-scraper`)
     → confirm the **author attended** from the post **text** (`is_attending`
     regex) → enrich → US + decision-maker → `event_attendance` signal.
   - Pivoted **Trigify → Apify** (cheaper, better data). Stores: `social_targets`,
     `event_keywords` (+ CRUD + API). Files: `auto_search/social/*`.
   - **Known result, not a bug:** an event run "qualified 0" because **`HIMSS26`
     this window = HIMSS *Europe*** → 7/9 attendees non-US (correctly dropped), the
     2 US ones were vendors (not provider ICP). **Fix = track US-provider events**
     (HFMA, AAHAM, ViVE, Becker's, MGMA), not a code change.
5. **Unified Run + Run-UX** — resolved the "2 runs" confusion: **one `Run` button**
   (top right) does connectors + social + events for the last 24h. The old
   "Monitored accounts" panel is now **"Social listening"** — a *setup* panel
   (manage accounts + event keywords) whose windowed scan is reframed as
   **"Back-fill"** (older posts), **not** a second run. Shared RunControl + one live
   banner.
6. **Jobs signal-stacking** — the latest, largest feature. See §5.

---

## 5. Jobs signal-stacking (latest feature — deep dive)

**Problem the user posed:** "We have 24 RCM titles to track. 50 rows each is a
huge credit cut, and a single routine posting clogs the pipe. I love signal
stacking — how do we play this efficiently without missing accounts?"

**Solution — tier every title, then gate qualification by tier.**

### Titles & tiers (`auto_search/connectors/job_postings.py`, `ESSENTIAL_RCM_TITLES`)
Each title is `EssentialTitle(query, role, strength, tier)`. The user gave the
authoritative **24 titles**:

- **CORE (11)** — a *single* posting qualifies (high-intent work Magical automates
  directly): Prior Auth, Authorization Coordinator, Insurance Verification,
  Eligibility, Claims Specialist, Claims Processor, Denials, Appeals, Revenue
  Cycle Specialist, Revenue Integrity, UM Nurse.
- **STANDARD (13)** — must **STACK** to qualify (higher-volume / clinical-adjacent /
  cross-industry titles): Medical Biller, Billing Specialist, Coder, CDI,
  Collections, Payment Posting, Patient Access Rep, Referral Coordinator, Intake
  Coordinator, Scheduling Coordinator, Care Coordinator, Patient Navigator,
  Clinical Reviewer.

*(Tier is one line to flip per title; the split is a judgment call the user can
re-bucket.)* Every posting also stamps `payload["tier"]` so the gate can read it.

### The gate (`auto_search/job_stacking.py`, pure + fully tested)
`stacking_decision(signals)` →
- **QUALIFY** if: any **non-job** signal present (the jobs gate must never suppress
  leadership/funding/social) **OR** ≥1 **core** posting **OR** ≥ `STACK_MIN` (=2)
  **standard** postings (a real revenue-cycle build-out).
- **PARK** if: a single standard posting and nothing above.
- **Fail-open**: missing/unknown tier is treated as core (never silently parks a
  real signal) — same stance as the job qualifier.

### Parked "watch" ledger
Single-standard companies are **parked**, not lost:
- Stored in **`parked_companies`** (Postgres table + JSON sidecar + protocol methods
  `upsert_parked` / `parked_companies`).
- **Self-correcting & display-only**: the qualify decision never reads it back (the
  wide pull window is the real memory). It's hidden once the company is
  `already_qualified` (graduated by any path) and **TTL-pruned** at 30 days.
- The runner re-evaluates parked companies **every run** for free — so a company
  **auto-qualifies the moment a 2nd role opens**.

### Why stacking works within one run — the wide window
`discovery_runner.JOBS_WINDOW_DAYS` (=14) makes the jobs connector pull a wider
"currently-open" window than other sources. RCM reqs stay open for weeks, so a
company's co-open roles land in the **same run** and stack in one decision — no
fragile cross-run signal merge needed. **The window only changes recency, not
row count, so widening it is ~free.**

### Cost levers (the real answer to "50 each is a lot")
1. **Board asymmetry** (`_boards_for`): **core** titles search **Indeed +
   LinkedIn** (max recall on high-value roles); **standard** titles search
   **Indeed only** (half the credits on the noisy, must-stack roles).
2. **Per-title rows 50 → 12** (`DISCOVERY_JOBS_MAX_ROWS`, default lowered in the
   runner) — the wide window gives recency without depth.
3. The **gate** spends the ~$0.10 Claude qualifier only on core hits + real
   build-outs; the long tail of one-off billers/coders costs **$0**.

**Net math:** 24 titles ≈ **~420 max scrape rows/run** (11×2 + 13×1 = 35 searches ×
12) vs the old 8-title config's **800**. More coverage, ~half the worst-case spend.

### UI (no emojis — board-facing)
- **`StackedHiringPill`** (`web/discovery/ui.jsx`) — an amber **"N RCM roles open"**
  pill with a **zap** icon; fires at **≥2 job postings**; leads the per-role chips
  on the company row.
- **`WatchStrip`** (`web/discovery/app.jsx`) — a subtle, **expandable** strip
  ("Watching N companies with a single open RCM role — they auto-qualify the
  moment a second role opens"), **clock** icon, on the Qualified tab; fed by
  `GET /api/discovery/parked`.
- Drawer (`web/discovery/drawer.jsx`) header shows "N RCM roles open" when stacked.
- (Emojis `🔥`/`👀` were used then **removed** per the board-facing rule → SVG icons.)

### API
`GET /api/discovery/parked` → `{ companies, count, stack_min, window_days }`.

### Env knobs
`DISCOVERY_JOBS_TITLES` (allowlist subset), `DISCOVERY_JOBS_STACK_MIN`,
`DISCOVERY_JOBS_WINDOW_DAYS`, `DISCOVERY_JOBS_PARK_TTL_DAYS`,
`DISCOVERY_JOBS_MAX_ROWS`, `APIFY_JOBS_ROWS`.

---

## 6. Testing & QA conventions

- **`pytest`** — 377 pass. Key suites: `tests/test_job_stacking.py`
  (gate/watch/pipeline-hook/JSON-store/runner), `tests/test_job_postings.py`
  (connector, board asymmetry, tiers), `tests/test_pipeline_gate.py`,
  `tests/test_discovery_runner.py`. Run: `PYTHONPATH=. python3 -m pytest -q`.
- **Offline JSX validation** (catches white-screens before deploy): transform every
  `web/discovery/*.jsx` with **babel-standalone** via `osascript -l JavaScript`.
  Pattern lives in `/tmp/jsxcheck.js` (loads `/tmp/babel-standalone.js`,
  `Babel.transform(code,{presets:['react']})`). **Always run this** after touching
  JSX.
- **Playwright UI suite** (`tests/ui/test_ui_smoke.py`) — seeds local Postgres, runs
  `schema.sql`, boots uvicorn, drives headless chromium. Asserts no console errors,
  the **stacking pill + watch strip render**, the scored "Why discovered" panel,
  and that an unknown-framework drawer doesn't white-screen. **Local only** (skips
  in CI without Postgres/chromium). Run: `PYTHONPATH=. python3 -m pytest tests/ui`.
- **`ruff`** must be clean: `python3 -m ruff check auto_search/ tests/`.

---

## 7. Deploy & infra state (as of 2026-06-09)

- **PR #1** (`feat/auto-search-layoffs` → `main`) — **MERGED** (`a20e47b`).
- **`discovery-api`** — **DEPLOYED & live** (Railway deploy `b99d98e7`, SUCCESS).
  Verified by `GET /api/discovery/parked` → `{stack_min:2, window_days:14}` (route
  only exists in new code). `parked_companies` auto-created on boot. Prod data
  intact (≈75 qualified / 324 total). URL:
  `https://discovery-api-production-dc7f.up.railway.app`.
- **`discovery-cron`** — **NOT redeployed** (still old 8-title code; schedule
  `0 14 * * 1-5`). **Deliberate**: keeps the nightly run from surprise-spending on
  24 titles before the user has watched a manual run. Redeploy when ready:
  `railway up --service discovery-cron`.
- **Deploy command:** `railway up --service discovery-api --detach`.
- **Verify a deploy:** `railway status --json` (check the service's
  `activeDeployments[].status == SUCCESS` and a fresh `createdAt`) + `curl` the
  health/new route. Basic-auth creds are in `railway variables --service
  discovery-api` (`BASIC_AUTH_USER` / `BASIC_AUTH_PASS`).

---

## 8. Conventions, gotchas & guardrails

- **In-browser JSX** → a syntax error white-screens the app. Run the offline babel
  transform (and the Playwright smoke test) before saying "done."
- **No emojis** in user-facing UI (board-facing). Use the `Icons.*` SVG set in
  `ui.jsx`. Monochrome `✓ ✕ ~ !` glyphs in the activity ticker are acceptable.
- **Cost discipline:** qualify-first-then-enrich; tier gates; low row caps; never
  re-run a paid call you can cache/skip. Don't fetch 50 rows when 12 will do.
- **Don't trigger the cron** unintentionally; **don't push to `main` without
  approval** (now merged via PR #1).
- **Fail-open** on classifier uncertainty (job qualifier, stacking tier) — the
  downstream company qualifier is the real backstop; never silently drop.
- **Model is Sonnet** (`claude-sonnet-4-5`).
- **Schema migrations are additive** + auto-applied on boot (`ensure_schema`). New
  tables must be `CREATE TABLE IF NOT EXISTS` so old services keep working.

---

## 9. Open items / next steps

1. **Redeploy `discovery-cron`** once the user is happy with a manual Run's cost —
   then the nightly job picks up the 24-title stacking logic.
2. **Add US healthcare-event keywords** (HFMA, AAHAM, ViVE, Becker's, MGMA) — the
   current `HIMSS26` keyword surfaces the European event.
3. Event extraction handles **author-attending** only; the "someone *else* is
   attending" (named third party) case is not handled — possible follow-up.
4. Longer term: a first-class **contacts** model (today a person is stored as a
   signal on the company).

---

## 10. File map (where to look)

| Area | File(s) |
|---|---|
| Jobs connector + 24 tiered titles + board asymmetry | `auto_search/connectors/job_postings.py` |
| Stacking gate (pure) | `auto_search/job_stacking.py` |
| Cheap job-level qualifier (prefilter) | `auto_search/job_qualifier.py` |
| Pipeline (defer/on_defer, gate, dedup) | `auto_search/pipeline.py` |
| Runner (orchestration, jobs window, parked wiring) | `auto_search/discovery_runner.py` |
| Company ICP qualifier (Claude + web_search) | `auto_search/qualifier.py` |
| Storage protocol + JSON impl (incl. parked) | `auto_search/db/repository.py` |
| Postgres impl (incl. parked) | `auto_search/db/postgres_repository.py` |
| Schema (parked_companies, social_targets, …) | `auto_search/db/schema.sql` |
| API (panel, run, /api/discovery/parked, social) | `auto_search/api/app.py` |
| Social listening (engagement + events) | `auto_search/social/*` |
| UI — company rows, pill, watch strip | `web/discovery/{ui,app,panel,drawer}.jsx` |
| UI — social listening control center | `web/discovery/socialMonitor.jsx` |
| Frontend API client | `web/discovery/api.js` |
| Tests | `tests/test_job_stacking.py`, `tests/test_job_postings.py`, `tests/ui/test_ui_smoke.py`, … |
