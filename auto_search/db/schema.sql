-- ════════════════════════════════════════════════════════════════════
-- Auto Search — discovery schema
-- ════════════════════════════════════════════════════════════════════
--
-- Design principles:
--   1. Store the VERDICT and PROVENANCE, not the raw firehose. We never
--      persist the full WARN dataset — only companies we actually evaluated.
--   2. Dedup is enforced by the DATABASE (UNIQUE constraints), not by app
--      code that can forget. Two layers: signal-level and company-level.
--   3. One Claude call per company, ever. The company-level row records the
--      verdict; re-seeing the company later never re-triggers qualification.
--
-- This schema is intentionally separate from the main `accounts` tables.
-- Discovery is System A; engagement is System B. The only bridge is
-- promotion: a human action that creates an `accounts` row from a
-- discovery_companies row. (Promotion FK added when accounts table lands.)
-- ════════════════════════════════════════════════════════════════════


-- ────────────────────────────────────────────────────────────────────
-- discovery_companies
--   One row per UNIQUE company ever seen by any connector.
--   This is the dedup ledger AND the qualification verdict store.
--   `normalized_name` is the dedup key (from normalize_company_name()).
-- ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS discovery_companies (
    id                    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- identity + dedup --------------------------------------------------
    normalized_name       TEXT NOT NULL,            -- dedup key, e.g. "acmehealth"
    display_name          TEXT NOT NULL,            -- "Acme Health, LLC"
    domain                TEXT,                      -- filled by qualifier if found

    -- qualification verdict (what the UI shows) -------------------------
    icp_status            TEXT NOT NULL DEFAULT 'pending'
        CHECK (icp_status IN ('pending','qualified','needs_review','disqualified','error')),
    segment               TEXT
        CHECK (segment IS NULL OR segment IN ('specialty','payer','health_system')),
    sub_segment           TEXT,
    company_type          TEXT,
    approximate_employees INTEGER,
    confidence            NUMERIC(3,2),
    reasoning             TEXT,                      -- Claude's justification
    evidence_url          TEXT,                      -- page the verdict leaned on
    decided_by            TEXT,                      -- 'rules' | 'llm' | 'rules+llm'

    -- lean firmo (only what the UI needs) -------------------------------
    hq_state              TEXT,
    hq_city               TEXT,

    -- human review workflow (separate from machine icp_status) ----------
    -- 'pending' until Galyna acts; never reset by re-qualification.
    review_status         TEXT NOT NULL DEFAULT 'pending'
        CHECK (review_status IN ('pending','promoted','rejected','deferred')),
    reviewed_at           TIMESTAMPTZ,               -- when Galyna decided
    rejection_reason      TEXT,                       -- set on reject

    -- lifecycle ---------------------------------------------------------
    first_seen_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    qualified_at          TIMESTAMPTZ,               -- when Claude ran
    promoted_at           TIMESTAMPTZ,               -- when a human promoted it
    promoted_account_id   BIGINT,                    -- FK to accounts (System B), later

    -- DEDUP LAYER 2: one row per company, full stop.
    CONSTRAINT uq_discovery_company UNIQUE (normalized_name)
);

-- Review queue: the UI lists qualified + needs_review, freshest first.
CREATE INDEX IF NOT EXISTS idx_disco_status_seen
    ON discovery_companies (icp_status, first_seen_at DESC);

-- Fast "qualified, by segment, best first" for the eventual dashboard.
CREATE INDEX IF NOT EXISTS idx_disco_qualified
    ON discovery_companies (segment, confidence DESC)
    WHERE icp_status = 'qualified';


-- ────────────────────────────────────────────────────────────────────
-- discovery_signals
--   WHY a company is in the funnel. One row per signal occurrence.
--   A company can have many (layoff in March + layoff in May + funding).
--   We keep these for provenance and for the UI's "why" panel — but lean:
--   just the few fields a human needs to judge the signal.
-- ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS discovery_signals (
    id                    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id            BIGINT NOT NULL
        REFERENCES discovery_companies (id) ON DELETE CASCADE,

    source                TEXT NOT NULL,             -- 'warntracker'
    signal_type           TEXT NOT NULL,             -- 'layoff'
    source_external_id    TEXT NOT NULL,             -- stable per-event id

    summary               TEXT,                      -- "135 laid off in Toledo, OH"
    signal_strength       NUMERIC(3,2),
    observed_at           TIMESTAMPTZ NOT NULL,      -- when the event happened
    ingested_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- minimal raw payload for audit — NOT the whole scraped blob
    payload               JSONB NOT NULL DEFAULT '{}',

    -- DEDUP LAYER 1: the same source event is never stored twice.
    CONSTRAINT uq_discovery_signal UNIQUE (source, source_external_id)
);

CREATE INDEX IF NOT EXISTS idx_signal_company
    ON discovery_signals (company_id);


-- ────────────────────────────────────────────────────────────────────
-- connector_runs
--   Operational heartbeat. "Did the 6am cron run? How many new companies?
--   Did it error?" Lets us monitor the pipeline without grepping logs.
-- ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS connector_runs (
    id                    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source                TEXT NOT NULL,
    started_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at           TIMESTAMPTZ,
    status                TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running','success','failed')),

    planned               INTEGER NOT NULL DEFAULT 0,   -- companies to qualify this run (progress denominator)
    rows_fetched          INTEGER NOT NULL DEFAULT 0,   -- raw rows from source
    new_companies         INTEGER NOT NULL DEFAULT 0,   -- not seen before
    signals_added         INTEGER NOT NULL DEFAULT 0,   -- new signal rows
    companies_qualified   INTEGER NOT NULL DEFAULT 0,   -- Claude said yes
    error_message         TEXT
);

-- Backfill for tables created before `planned` existed (idempotent).
ALTER TABLE connector_runs ADD COLUMN IF NOT EXISTS planned INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_runs_source_started
    ON connector_runs (source, started_at DESC);


-- ── ABM target list ───────────────────────────────────────────────────────
-- The sales team's uploaded target accounts (the Q2 workbook). Discovery
-- companies are matched against this list; a match that also has a live buying
-- signal is the highest-value lead there is. Replaced wholesale on each upload.
CREATE TABLE IF NOT EXISTS abm_targets (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name           TEXT NOT NULL,
    aliases        JSONB NOT NULL DEFAULT '[]',
    keys           JSONB NOT NULL DEFAULT '[]',   -- normalized match keys (name + aliases)
    domain         TEXT,
    state          TEXT,
    segment        TEXT,
    source_sheet   TEXT,
    definitive_id  TEXT,
    uploaded_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_abm_targets_domain ON abm_targets (domain);

-- Monitored LinkedIn accounts: profiles/companies whose post engagers we scrape
-- (Magical's own + competitors). url_key is the normalized dedup key.
CREATE TABLE IF NOT EXISTS social_targets (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    url_key       TEXT NOT NULL UNIQUE,
    linkedin_url  TEXT NOT NULL,
    label         TEXT,
    kind          TEXT NOT NULL DEFAULT 'competitor',   -- own | competitor
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Event/conference keywords we search public posts for, to find ATTENDEES.
-- kw_key is the normalized dedup key.
CREATE TABLE IF NOT EXISTS event_keywords (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kw_key        TEXT NOT NULL UNIQUE,
    keyword       TEXT NOT NULL,
    label         TEXT,
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Stacking watch ledger: companies the jobs gate PARKED — a single open
-- "standard" RCM posting, not yet enough to spend the company qualifier. They
-- are NOT in discovery_companies (never qualified); this is a lightweight
-- watch list so a parked company isn't lost and auto-qualifies once it stacks
-- (a 2nd open role) on a later run. Display-only — the qualify decision never
-- reads it back. Rows are pruned by last_seen_at TTL and hidden once the
-- company graduates into discovery_companies.
CREATE TABLE IF NOT EXISTS parked_companies (
    company_key     TEXT PRIMARY KEY,          -- normalized company name
    name            TEXT NOT NULL,
    domain          TEXT,
    role            TEXT,                       -- the single standard role bucket
    roles           JSONB NOT NULL DEFAULT '[]',
    postings        INTEGER NOT NULL DEFAULT 1,
    state           TEXT,
    city            TEXT,
    sample_url      TEXT,                       -- a posting link for the watch UI
    sample_title    TEXT,
    observed_at     TEXT,                       -- posting timestamp (display only)
    first_parked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_parked_last_seen
    ON parked_companies (last_seen_at DESC);
