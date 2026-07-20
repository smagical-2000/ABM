-- ════════════════════════════════════════════════════════════════════════════
-- Campaign automation — schema (Reply.io enrollment)  ·  Phase 3
-- ════════════════════════════════════════════════════════════════════════════
-- A self-contained module. Coexists with the discovery / scoring / engagement
-- stores in the same database but shares NO tables and holds NO foreign keys
-- into them: `account_id` is the same SOFT text reference the engagement store
-- uses, so this module can never lock or break the live tables.
--
-- Contacts are NOT copied here — they stay in Reply.io (and are mirrored by the
-- engagement store). This module records only DECISIONS:
--   • campaign_sequences   — which Reply.io campaign each ICP sequence key maps
--     to. Data, not code: sequences are authored in the Reply.io UI, so their
--     ids arrive at runtime (Galyna assigns them in the Campaigns tab).
--   • campaign_enrollments — the ledger: one row per (account, contact,
--     campaign) push, so re-runs are idempotent and every send is auditable.
--
-- Every statement is idempotent (CREATE ... IF NOT EXISTS); runs on API boot.
--
-- Changelog
--   2026-07-05  v1  initial: sequences mapping + enrollment ledger.
-- ════════════════════════════════════════════════════════════════════════════


-- ── SEQUENCE MAPPING ─────────────────────────────────────────────────────────
-- sequence_key values come from auto_search/campaigns/catalog.py. A row with an
-- empty/NULL campaign_id means "not mapped yet" — the runner skips that ICP and
-- the tab shows it as needing setup.
CREATE TABLE IF NOT EXISTS campaign_sequences (
    sequence_key   TEXT PRIMARY KEY,
    campaign_id    TEXT,                    -- Reply.io campaign id (text: soft ref)
    campaign_name  TEXT,                    -- display name at assignment time
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ── ENROLLMENT LEDGER ────────────────────────────────────────────────────────
-- One row per push attempt that reached Reply.io (or deliberately did not):
--   enrolled     the contact was created/pushed into the campaign
--   skipped_409  Reply.io said "already in a sequence" — expected, terminal
--   failed       the write errored (kept for audit; a re-run may retry it)
-- Dry-runs are NEVER persisted — the ledger is what actually happened.
CREATE TABLE IF NOT EXISTS campaign_enrollments (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id    TEXT NOT NULL,            -- soft ref (acc_/abm_/csv_ key)
    account_name  TEXT,
    contact_ext   TEXT NOT NULL,            -- engagement_contacts.external_id
    email         TEXT,
    channel       TEXT NOT NULL DEFAULT 'email',   -- 'email' | 'linkedin' (later)
    sequence_key  TEXT NOT NULL,            -- catalog key at enroll time
    campaign_id   TEXT NOT NULL,            -- Reply.io campaign enrolled into
    status        TEXT NOT NULL
        CHECK (status IN ('enrolled','skipped_409','failed')),
    detail        JSONB NOT NULL DEFAULT '{}'::jsonb,
    trigger       TEXT,                     -- 'auto' | 'manual' — who fired it
    enrolled_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- idempotency: one decision per (account, contact, campaign), full stop.
    CONSTRAINT uq_campaign_enrollment UNIQUE (account_id, contact_ext, campaign_id)
);

CREATE INDEX IF NOT EXISTS idx_enroll_account
    ON campaign_enrollments (account_id, enrolled_at DESC);
CREATE INDEX IF NOT EXISTS idx_enroll_recent
    ON campaign_enrollments (enrolled_at DESC);


-- ── PER-CHANNEL SEQUENCE MAPPING (multichannel orchestration) ────────────────
-- campaign_sequences above stays the EMAIL mapping (unchanged, zero migration).
-- Other channels (linkedin via HeyReach, sms via Twilio later) map here:
-- one row per (sequence key, channel) -> the executor tool's campaign/flow id.
CREATE TABLE IF NOT EXISTS campaign_channel_sequences (
    sequence_key   TEXT NOT NULL,
    channel        TEXT NOT NULL CHECK (channel IN ('linkedin','sms')),
    campaign_id    TEXT,                    -- HeyReach campaign id / Twilio flow id
    campaign_name  TEXT,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (sequence_key, channel)
);


-- ── STOP-RULE LEDGER (reply anywhere -> pause everywhere) ────────────────────
-- One row per stop ACTION taken, so cross-channel pauses are auditable and the
-- sweep is idempotent (won't re-fire for the same account+channel+reason).
CREATE TABLE IF NOT EXISTS campaign_stops (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id   TEXT NOT NULL,
    channel      TEXT NOT NULL,             -- the channel that was STOPPED
    reason       TEXT NOT NULL,             -- e.g. 'reply:email' | 'reply:linkedin'
    detail       JSONB NOT NULL DEFAULT '{}'::jsonb,
    stopped_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_campaign_stop UNIQUE (account_id, channel, reason)
);
