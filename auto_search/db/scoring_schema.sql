-- ────────────────────────────────────────────────────────────────────
-- scored_accounts
--   The scoring phase store. One denormalized row per account across its
--   whole lifecycle (queued -> scoring -> scored / error), so the Scored
--   dashboard reads it with a single query. Score + independent QA live
--   inline because the UI always renders them together.
--
--   Source of an account:
--     discovery  promoted from the Discovery panel (carries its signals)
--     csv        imported from a Definitive Healthcare export
-- ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS scored_accounts (
    account_id            TEXT PRIMARY KEY,
    source                TEXT NOT NULL CHECK (source IN ('discovery','csv')),
    discovery_company_key TEXT,
    name                  TEXT NOT NULL,
    segment               TEXT NOT NULL,
    framework             TEXT NOT NULL,
    domain                TEXT,
    sub_segment           TEXT,
    approximate_employees INTEGER,

    -- known facts (CSV columns / discovery firmographics) + carried intent
    firmographics         JSONB NOT NULL DEFAULT '{}'::jsonb,
    discovery_signals     JSONB NOT NULL DEFAULT '[]'::jsonb,

    state                 TEXT NOT NULL DEFAULT 'queued'
        CHECK (state IN ('queued','scoring','scored','error')),
    -- sub-step while state='scoring' (scoring | verifying), for the progress UI
    phase                 TEXT,

    -- the score (NULL until state='scored')
    total                 INTEGER,
    max_total             INTEGER NOT NULL,
    tier_band             TEXT,          -- high | medium | low | out
    tier_label            TEXT,          -- "Tier 1" | "High Fit" | ...
    dimensions            JSONB,         -- [{key,label,score,max,summary,flags}]
    recommendation        TEXT,
    qa                    JSONB,         -- {status,notes,corrections,tier_changing}
    model                 TEXT,
    -- measured USD spend for this account (scorer + QA), for the cost meter
    cost_usd              DOUBLE PRECISION NOT NULL DEFAULT 0,
    -- which CSV import this account came in on (filename + time), so a user can
    -- filter + export exactly the batch they uploaded. NULL for discovery.
    import_label          TEXT,
    error_message         TEXT,

    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    scored_at             TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_scored_state
    ON scored_accounts (state, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_scored_tier
    ON scored_accounts (tier_band)
    WHERE state = 'scored';

-- Additive migrations for stores created before these columns existed.
ALTER TABLE scored_accounts ADD COLUMN IF NOT EXISTS phase TEXT;
ALTER TABLE scored_accounts ADD COLUMN IF NOT EXISTS cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE scored_accounts ADD COLUMN IF NOT EXISTS import_label TEXT;
