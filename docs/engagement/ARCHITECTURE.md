# Engagement Intelligence — Architecture (Reply.io)

The HOW for the PRD in this folder. Engagement is a **self-contained module** that
plugs into the ABM system without touching it.

## Principles
- **Reuse**: `auto_search/normalize` (domain + company-name keys), the `auto_search/abm`
  matcher, the repo-factory pattern. Don't reinvent.
- **Dual repo** (JSON for tests/CI, Postgres for prod) like discovery + scoring.
- **Pure scorer** in Python = the single source of truth for weights + tiers.
- **ELT**: land raw → transform (re-transformable without re-pulling).
- **Coexists, never couples**: own schema file + repo; `account_id` is a soft text
  reference (no FK), re-stamped each sync from durable keys, so it self-heals and
  can never lock/break the live ABM/scoring tables.

## Module layout
```
auto_search/db/engagement_schema.sql        # idempotent schema (own module)   [M-A ✅]
auto_search/db/engagement_repository.py      # protocol + JSON + Postgres impls  [M-A ✅]
auto_search/engagement/replyio_client.py     # read-only v3 client               [M-B]
auto_search/engagement/ingest.py             # raw -> normalized                 [M-C]
auto_search/engagement/cross.py              # contact -> scored/ABM account     [M-D]
auto_search/engagement/scoring.py            # PURE: points map + tier_for()     [M-E]
auto_search/api/app.py                       # GET/POST /api/engagement*         [M-F]
web/discovery/engagement.jsx (+ ported DS)   # console: Accounts/Inbox/drawer    [M-G]
tests/test_engagement_*.py + evals
```

## Database (see `auto_search/db/engagement_schema.sql`)
- `engagement_raw` — verbatim payloads (ELT).
- `engagement_contacts` — one per contact: identity, cross-match, per-window send
  counts (delivered/opened/clicked/replied/bounced) for rates.
- `engagement_events` — one per contact × meaningful touch (click/reply/meeting);
  PK `(source, external_id="source:kind:contactId")` makes re-sync idempotent and
  prevents score inflation.
- `engagement_sync_state` — per-source cursor + last-run stats.
- `engaged_accounts` (view) — read-time rollup; **tier assigned by the Python scorer**.

## Data flow
`POST /api/engagement/sync` → Reply.io client (contacts + reporting/emails, 30-day
window, paginated, backoff) → land raw → normalize to contacts + events → cross each
contact (domain → scored → ABM, then name; unresolved → null) → stamp `account_id`
→ `engaged_accounts` view → `GET /api/engagement` → console.

## Repository (M-A, shipped)
`EngagementRepository` protocol + `EngagementJsonRepository` (file, tests) +
`EngagementPostgresRepository` (psycopg3, pooled). `get_engagement_repository()`
returns PG when `DATABASE_URL` is set, else JSON; fails closed in production.
Methods: `ensure_schema, land_raw, upsert_contact, add_event (idempotent),
engaged_accounts, events_for_account, contacts(unresolved_only), recent_events,
get/set_sync_state, delete_all`.

## Later modules (per-milestone detail in their PRs)
- **client** — read-only Bearer; `contacts()`, `email_activity(from,to)`; pagination
  + 429 backoff; 30-day (60 fallback) window.
- **cross** — one index over scored ∪ ABM; returns `(account_id, tier, lists[])`;
  reuses `normalize_company_name` + `clean_domain` + the ABM matcher.
- **scoring** — `POINTS={click:1, reply:6, meeting_booked:10}`, `points_for`, `tier_for`.
- **API** — `POST /api/engagement/sync`, `GET /api/engagement`, `GET /api/engagement/{id}`.
- **UI** — port DS tokens/components into `web/discovery/`; `engagement.jsx`; nav mount.

## Verifiers (the inner loop)
A schema applies + documented · B client unit tests · C ingest idempotent · D cross
tests vs seeded scored+ABM · E pure-scorer tests · F API via TestClient · G Playwright
smoke + visual diff. Every bug → `evals/bugs.json`.

## Coexistence checklist (won't bite later)
- No shared tables, no FK into discovery/scoring → zero blast radius.
- Source-agnostic events → new sources are new connectors, no schema change.
- View → materialized view is a drop-in swap behind the same API if volume grows.
- Schema-on-boot is documented + changelog'd; a clean Alembic cutover is possible
  repo-wide later without reshaping these tables.
