-- ════════════════════════════════════════════════════════════════════════════
-- Engagement Intelligence — schema (Reply.io, read-only)  ·  Phase 2
-- ════════════════════════════════════════════════════════════════════════════
-- A self-contained module. It coexists with the discovery store (schema.sql) and
-- the scoring store (scoring_schema.sql) in the same database, but shares NO
-- tables and holds NO foreign keys into them.
--
--   `account_id` is a SOFT reference (plain text) to a scored/ABM account. It is
--   re-stamped on every sync from durable keys (email domain / normalized company
--   name), so it self-heals if a scored/ABM id is ever re-keyed — and the
--   engagement module can never lock or break the live ABM/scoring tables.
--
-- Every statement is idempotent (CREATE/ALTER ... IF NOT EXISTS), so running this
-- on each boot is safe and a fresh database self-initialises.
--
-- Design notes
--   • ELT: land raw Reply.io payloads first, transform second — so a mapping fix
--     never requires re-pulling from the API.
--   • engagement_events holds one row per contact × MEANINGFUL touch
--     (click / reply / meeting_booked). Deliveries and opens are NOT stored per
--     row (that is millions of low-signal rows); their COUNTS live on the contact
--     for rate math. external_id is "<channel>:<kind>:<contactId>" (e.g.
--     "email:reply:123") — source is its own column, so it is not repeated. The
--     primary key (source, external_id) enforces one row per contact × kind, so
--     re-syncs are idempotent and a long contact list cannot inflate a score.
--   • engagement_events is source-agnostic: a future source (SFDC, the tofu/bofu
--     sheet, podcast) writes the same shape with a different `source`.
--   • The heat TIER is assigned by the pure Python scorer (engagement/scoring.py),
--     never in SQL, so the weights + thresholds stay a single source of truth.
--
-- Changelog
--   2026-06-14  v1  initial: raw landing, contacts, events, sync_state, view.
-- ════════════════════════════════════════════════════════════════════════════


-- ── RAW landing ──────────────────────────────────────────────────────────────
-- Verbatim source payloads, for replay + audit. We transform from here, so a
-- normalization bug is re-runnable without another API pull. Append-only; a
-- retention job prunes by fetched_at once it grows (follow-up, not needed at the
-- current 30-day-window volume).
CREATE TABLE IF NOT EXISTS engagement_raw (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source      TEXT NOT NULL DEFAULT 'replyio',
    kind        TEXT NOT NULL,                 -- 'email_activity' | 'contact'
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    window_from DATE,
    window_to   DATE,
    payload     JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_eng_raw_fetched ON engagement_raw (fetched_at DESC);


-- ── CONTACTS ─────────────────────────────────────────────────────────────────
-- One row per source contact: identity, the cross-match result, and per-window
-- send-stat COUNTS used for open-rate / reply-rate (kept here instead of as
-- per-row events to avoid bloat).
CREATE TABLE IF NOT EXISTS engagement_contacts (
    source         TEXT NOT NULL DEFAULT 'replyio',
    external_id    TEXT NOT NULL,              -- Reply.io contactId
    email          TEXT,
    email_domain   TEXT,                       -- clean_domain(email)
    company        TEXT,
    company_key    TEXT,                       -- normalize_company_name(company)
    title          TEXT,
    meeting_booked BOOLEAN NOT NULL DEFAULT false,
    opted_out      BOOLEAN NOT NULL DEFAULT false,

    -- per-window send-stat counts (rate math; no per-event bloat)
    sent           INTEGER NOT NULL DEFAULT 0,
    delivered      INTEGER NOT NULL DEFAULT 0,
    opened         INTEGER NOT NULL DEFAULT 0,
    clicked        INTEGER NOT NULL DEFAULT 0,
    replied        INTEGER NOT NULL DEFAULT 0,
    bounced        INTEGER NOT NULL DEFAULT 0,

    -- crossing result (re-stamped each sync; NULL account_id = unresolved -> review)
    account_id     TEXT,
    match_tier     TEXT,                        -- 'domain' | 'name' | NULL
    matched_lists  JSONB NOT NULL DEFAULT '[]', -- e.g. ["scored","abm"]

    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, external_id)
);
CREATE INDEX IF NOT EXISTS idx_eng_contact_account ON engagement_contacts (account_id);
CREATE INDEX IF NOT EXISTS idx_eng_contact_domain  ON engagement_contacts (email_domain);
CREATE INDEX IF NOT EXISTS idx_eng_contact_key     ON engagement_contacts (company_key);


-- ── EVENTS ───────────────────────────────────────────────────────────────────
-- Source-agnostic meaningful touches (click / reply / meeting_booked). The PK
-- enforces "one row per contact × kind", so re-syncs are idempotent and scores
-- cannot inflate from repeated sends.
CREATE TABLE IF NOT EXISTS engagement_events (
    source       TEXT NOT NULL DEFAULT 'replyio',
    external_id  TEXT NOT NULL,                -- '<channel>:<kind>:<contactId>' e.g. 'email:reply:123'
    channel      TEXT NOT NULL,                -- 'email'
    kind         TEXT NOT NULL,                -- 'click' | 'reply' | 'meeting_booked'
    points       INTEGER NOT NULL DEFAULT 0,   -- assigned by the pure scorer
    contact_ext  TEXT,                         -- engagement_contacts.external_id
    company      TEXT,
    account_id   TEXT,                         -- denormalized at cross time; NULL=unresolved
    campaign     TEXT,
    occurred_at  TIMESTAMPTZ NOT NULL,         -- source delivery / activity date
    raw          JSONB NOT NULL DEFAULT '{}',
    ingested_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, external_id)
);
CREATE INDEX IF NOT EXISTS idx_eng_event_account  ON engagement_events (account_id);
CREATE INDEX IF NOT EXISTS idx_eng_event_occurred ON engagement_events (occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_eng_event_contact  ON engagement_events (contact_ext);


-- ── SYNC state ───────────────────────────────────────────────────────────────
-- One row per source: the cursor + last-run stats (powers the "last synced" UI
-- marker and lets an incremental pull resume from window_to).
CREATE TABLE IF NOT EXISTS engagement_sync_state (
    source         TEXT PRIMARY KEY,
    last_synced_at TIMESTAMPTZ,
    window_from    DATE,
    window_to      DATE,
    status         TEXT,                        -- 'running' | 'success' | 'failed'
    stats          JSONB,                       -- per-run counts; NULL until set
    error          TEXT
);
-- Idempotent: corrects any table created before `stats` was made nullable.
ALTER TABLE engagement_sync_state ALTER COLUMN stats DROP NOT NULL;


-- ── ENGAGED-ACCOUNTS rollup (derived at read time) ───────────────────────────
-- score = SUM(points): because events are one-per-contact-per-kind, this already
-- counts each contact's click(+1)/reply(+6)/meeting(+10) at most once. Rates come
-- from the contact send-stat counts. The Python scorer turns `score` into a tier.
CREATE OR REPLACE VIEW engaged_accounts AS
WITH e AS (
    SELECT account_id,
           SUM(points)                                   AS score,
           COUNT(*) FILTER (WHERE kind = 'click')         AS clicks,
           COUNT(*) FILTER (WHERE kind = 'reply')         AS replies,
           COUNT(*) FILTER (WHERE kind = 'meeting_booked') AS meetings,
           MAX(occurred_at)                              AS last_touch
    FROM engagement_events
    -- Exclude retired signal kinds (SAO, replaced by meeting_booked in 2026-06):
    -- historical rows stay for audit but never count toward heat. Keep this literal
    -- in sync with DEPRECATED_KINDS in engagement/scoring.py.
    WHERE account_id IS NOT NULL
      AND kind <> 'sales_accepted_opportunity'
    GROUP BY account_id
),
c AS (
    SELECT account_id,
           COUNT(*)        AS contacts,
           SUM(delivered)  AS delivered,
           SUM(opened)     AS opened,
           SUM(replied)    AS replied_sends
    FROM engagement_contacts
    WHERE account_id IS NOT NULL
    GROUP BY account_id
)
SELECT COALESCE(e.account_id, c.account_id)      AS account_id,
       COALESCE(e.score, 0)                       AS score,
       COALESCE(e.clicks, 0)                      AS clicks,
       COALESCE(e.replies, 0)                     AS replies,
       COALESCE(e.meetings, 0)                    AS meetings,
       COALESCE(c.contacts, 0)                    AS contacts,
       c.delivered,
       c.opened,
       c.replied_sends,
       e.last_touch
FROM e
FULL OUTER JOIN c USING (account_id);
