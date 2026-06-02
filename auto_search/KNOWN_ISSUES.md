# Auto Search — known issues & deferred work

Tracked items from code review that are intentionally **not** done yet, with
rationale. Listed so reviewers know they're acknowledged, not missed.

## Deferred until promotion is built

- **Domain-first account matching at promotion.** The locked platform dedup
  rule is: match on `accounts.domain` first, then `normalized_name`, then a
  fuzzy review queue. Auto Search now *captures* `domain` during
  qualification (stored on `discovery_companies.domain`) so it's ready — but
  the promotion step that performs the match against `accounts` doesn't exist
  yet (no `accounts` table connected). **Do not promote without implementing
  domain-first matching**, or it will create duplicate accounts / fail to
  merge with Definitive imports.

## Shipped since the original review

- **PostgresRepository — DONE.** `db/postgres_repository.py` implements the
  `DiscoveryRepository` protocol (psycopg3, sync, pooled) against `schema.sql`,
  with idempotent `ensure_schema()` on boot. `get_repository()` selects it when
  `DATABASE_URL` is set, else the JSON-file repo. No call-site changes.
- **HTTP API auth — DONE.** `api/auth.py` adds Basic auth, enabled iff
  `BASIC_AUTH_USER`/`PASS` are set (so localhost stays open, deploys are gated);
  `/api/health` is exempt for the platform healthcheck.

## Deferred until accounts table / workers

- **Real promotion.** `ReviewService.promote()` records `review_status=
  'promoted'` and returns a STUB account id — it does NOT create an `accounts`
  row or run scoring yet. Needs the accounts table + **domain-first matching**
  (Q1) before it's wired, or it will create duplicate accounts. The UI's
  auto-bulk-promote timer is OFF by default for the same reason.
- **MTD cost guardrails.** The architecture calls for a hard stop at $500
  MTD on LLM spend. The test script *estimates* per-run cost but nothing
  enforces a monthly ceiling yet. Needs a shared cost ledger + pre-call check
  once the pipeline runs unattended on a cron.
- **Qualifier client lifecycle.** `qualifier._client` is a module-level
  `AsyncAnthropic` singleton — fine for the CLI/test path, but a long-lived
  worker pool should manage its lifecycle (create on startup, close on
  shutdown) rather than lazy-init a global.

## Leadership connector (SignalBase)

- **Industry filter is client-side.** SignalBase's `industry`/`categories`
  params are silently ignored by the Apify run-sync path (only `positions`,
  `countries`, `seniorities`, date work server-side). We narrow by `positions`
  server-side and filter healthcare industry in Python. If SignalBase is ever
  called via a live Standby container, the `industry` hint we already send
  would filter server-side and cut credit use further.
- **`positions` is free-text** and matches sub-leadership roles too (e.g.
  "Revenue Cycle Analyst"); the connector drops these via `_NON_LEADER_MARKERS`.
  Tune that list + `_TITLE_PHRASES` as Galyna refines the target roles.

## Accepted as-is (documented, low priority)

- **Model split.** `scorer.py` uses Opus (deep scoring); the qualifier uses
  Sonnet via `ANTHROPIC_MODEL` (cheap classification). Intentional — different
  jobs, different cost/quality trade-offs. Documented in `.env.example`.
- **Dependency pinning.** `requirements.txt` uses lower bounds. Pin exact
  versions in a lockfile before deploying to Railway for reproducible builds.
- **Prompt injection surface.** Scraped company names flow into the LLM prompt
  unescaped. Low risk for an internal tool over government WARN data; revisit
  if a source becomes adversarial.
- **Trace files contain company data.** `data/qualifier_traces/` is gitignored
  but is debug-sensitive — don't sync to shared drives.
