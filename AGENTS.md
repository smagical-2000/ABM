# AGENTS.md

> The thin root for any AI agent working in this repo (Claude Code, Cursor, Codex, Devin...).
> Read this first. It tells you how to navigate and how we work. It points to the deep
> docs; it deliberately does not duplicate them. Open standard: https://agents.md

## What this is

**ABM Discovery + Scoring** — Magical's account-based-marketing intelligence platform for
US healthcare. It finds healthcare companies showing buying signals (hiring, leadership
changes, funding, layoffs, M&A, social engagement, industry news), scores buying **intent**
deterministically, runs deep AI **fit** scoring on the promising ones, and surfaces them to
the sales/marketing operator. Fit x Intent, like 6sense/Demandbase: one pipeline, two lenses.

- **Phase 1 (LIVE, in production on Railway):** Discovery (signal scan -> intent rank ->
  qualify), Scoring (deep fit research), News -> sales plays, Watch list (low-intent parking).
- **Phase 2 (NEXT):** Engagement Intelligence — capture multi-channel engagement (SFDC,
  Reply.io, Podcast, Airtable), score into heat buckets, cross to scored accounts, feed back
  into intent. **Spec: `docs/ENGAGEMENT_ARCHITECTURE.md`.** Tickets: the Linear project
  "ABM Engagement Intelligence" (team AGT).

## Hard constraints — read before you touch code

- **Backend:** FastAPI with **sync** handlers (run in a threadpool), Postgres 16, on Railway.
  Entry point: `auto_search/api/app.py`.
- **Frontend:** `web/discovery/` is React 18 + Babel **standalone, compiled IN THE BROWSER**.
  There is **no build step and no Node** on this machine. A JSX syntax error white-screens the
  whole app and will NOT appear in pytest. Always transpile-check JSX before deploy (see Verify).
  Each `<script type="text/babel">` is self-scoped; components are shared via `window.X = X`.
  Only use an icon that exists in `ui.jsx` (`Icons.*`) or the row renders blank.
- **Repos are dual:** `auto_search/db/repository.py` (JSON, used by tests) and
  `auto_search/db/postgres_repository.py` (prod). Every repo method must exist in **both** and
  behave identically. Schema is `auto_search/db/schema.sql` (all `CREATE/ALTER ... IF NOT EXISTS`,
  runs on API boot).
- **Secrets:** never commit. Read keys via `os.getenv(...)` only. `.env` is gitignored and
  points at the LOCAL Postgres.
- **Spend is guarded:** every paid LLM/Apify call goes through `auto_search/scoring/spend_guard.py`
  + `budget.py`. Monthly budgets hard-stop. Never bypass. See `docs/SPEND_GUARDRAILS.md`.
- **The product UI is operator/CEO-facing: no emojis in it.**

## How we build here (the pipeline — follow it, right-sized)

Agentic discipline, not vibe coding. Plan more than you build. Per feature, scaled to its size
(a one-line fix skips most of this; a connector does all of it):

1. **Requirements (light):** capture the *what* (a short note / the Linear ticket). Don't pre-decompose.
2. **Scope:** how it plugs into existing modules. **Reuse before you rebuild** (see the Map).
3. **Branch per feature:** `feat/<thing>`. One feature, one small PR. Do NOT pile unrelated work on one branch.
4. **Build.**
5. **Verify is the inner loop — run these BEFORE you say "done":**
   - `python3 -m ruff check auto_search/ scripts/ tests/`
   - `python3 -m pytest -q` — and add a test for what you changed.
   - JSX changed? Transpile-check it (a syntax error white-screens prod). UI behaviour changed?
     `python3 -m pytest tests/ui/test_ui_smoke.py` (Playwright; needs local Postgres + chromium).
6. **Fresh-context review:** a second pass with clean eyes (a sub-agent) + a security pass on any
   secret/auth/API-key handling.
7. **PR -> CI green -> human review -> merge to `main`.** CI is `.github/workflows/ci.yml`.
8. **Deploy + verify on the live URL:** `railway up --service discovery-api --detach`.
   NEVER redeploy `discovery-cron` unless that is the intent (it controls daily spend).
   See `docs/DEPLOY_RAILWAY.md`.
9. **Any bug you find/fix -> record it in `evals/bugs.json`** with the test that now guards it.

## Map — where things live

| Concern | Where |
|---|---|
| Deterministic buying-**intent** score (Hot/Watch) | `auto_search/priority.py` |
| Self-cleaning lifecycle (Watch -> needs_review -> auto-reject, promote-on-reheat, TTL) | `auto_search/lifecycle.py` |
| Jobs stacking gate (park lone single-standard hires) | `auto_search/job_stacking.py` |
| RCM title taxonomy (role -> tier, shared by connector + scorer) | `auto_search/rcm_titles.py` |
| Discovery pipeline (connector -> dedup -> qualify) | `auto_search/pipeline.py`, `discovery_runner.py` (panel run), `scripts/run_discovery.py` (cron) |
| Signal connectors | `auto_search/connectors/` (job_postings, leadership_changes, acquisitions, funding, warntracker) |
| Social listening (Apify post-engagers, competitor gate) | `auto_search/social/` |
| News -> sales plays | `auto_search/news/` |
| Deep **fit** scoring (engine, QA, frameworks) | `auto_search/scoring/` |
| Spend guard + monthly budgets | `auto_search/scoring/spend_guard.py`, `budget.py` |
| ABM target-sheet matching | `auto_search/abm/` |
| Warm intros (Apollo, free) | `auto_search/intros/` |
| All HTTP endpoints + the panel annotation (intent, watch-list filter, TTL) | `auto_search/api/app.py` |
| Web UI | `web/discovery/` — `app.jsx` (shell+nav), `panel.jsx` (rows), `parked.jsx` (Watch list), `scoringUI.jsx`, `drawer.jsx`, `news.jsx`, `ui.jsx` (shared components + `Icons`) |
| Data layer | `auto_search/db/` (`repository.py` JSON, `postgres_repository.py`, `schema.sql`) |

## Deep docs (read on demand)

- `docs/FINAL_ARCHITECTURE.md` — the locked V1 architecture (source of truth for the data model).
- `docs/ABM_PROJECT_ARCHITECTURE.md` — the ABM platform architecture.
- `docs/ENGAGEMENT_ARCHITECTURE.md` — **Phase 2** engagement spec (the connectors you may be here to build).
- `docs/SPEND_GUARDRAILS.md` · `docs/DEPLOY_RAILWAY.md` · `docs/QA_CHECKLIST.md` · `docs/DEDUP.md` · `docs/ABM_MATCHING.md`
- `KNOWN_ISSUES.md` (root) — current known issues. `README.md` / `ONBOARDING.md` — human onboarding.
- `evals/bugs.json` — every notable bug + the test that guards it. Read it to learn the gotchas.

## Cold-start note

The git history is clean and descriptive (`git log --oneline`). The active branch is a feature
branch off `main`; check `git status -sb`. (Claude Code on this machine also auto-loads richer
session memory from `~/.claude/.../memory/MEMORY.md`; other tools rely on this file + the docs.)
