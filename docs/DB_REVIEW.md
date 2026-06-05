# Database review

**Verdict: clean for v1.** The two-store model is intentional and correct for
this stage. No restructuring needed before the board demo.

## What exists

Two stores in one Postgres, decoupled on purpose:

- **Discovery** (`auto_search/db/schema.sql`): `companies`, `signals`,
  `connector_runs`. Source-of-truth for "what did we find and qualify."
  Dedup via UNIQUE constraints (see DEDUP.md).
- **Scoring** (`auto_search/db/scoring_schema.sql`): `scored_accounts` — one
  denormalized row per account across its whole lifecycle (queued → scoring →
  scored / error), with the score, QA, dossier, and cost inline. The Scored
  dashboard reads it in a single query.

**Bridge:** promote copies a qualified discovery company into a `scored_account`
keyed `acc_{company_key}`, carrying its qualification research as known facts.
The two phases stay independent — discovery can change without touching scoring.

## Why two stores (not one)

- The phases have different shapes and lifecycles (signals/runs vs a per-account
  state machine), different read patterns, and different owners in the code
  (`services/review.py` vs `scoring/service.py`).
- A repository Protocol fronts each (`get_repository`, `get_scoring_repository`),
  so JSON-for-dev and Postgres-for-prod swap with one env var, and the API never
  reaches across stores.
- Denormalizing the scoring row (score + QA + dossier inline) keeps the
  board-facing read fast and the code simple. No joins on the hot path.

## Deferred (not needed for v1)

- **Unified `accounts` table** — a canonical entity that both discovery and
  scoring reference, enabling name/domain merge (DEDUP.md gap). Build when
  multi-source dedup or per-account history matters.
- **Engagement tables** — outreach, owner/campaign assignment, activity. Out of
  scope by direction; would be its own store.
- **Audit/event log** — who scored/promoted whom and when. Partially covered by
  `cost_events` once SPEND guardrails land (see SPEND_GUARDRAILS.md); a full
  audit log is a later concern.

## Risks / watch items

- `scored_accounts` is denormalized, so a rubric change does not retro-update old
  rows — they show their original score until re-scored (by design).
- JSON repos are dev-only; production must use Postgres (`DATABASE_URL`). The
  factory + a production guard enforce this.
