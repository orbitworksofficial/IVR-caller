-- ============================================================================
-- Migration 0007: AI IVR Caller — Call Tables
-- Author: Hammad Ahmed (AI/ML Engineer)
--
-- Extends the existing chatbot platform with call-specific tables.
-- One brain, two mouths — same tenant/knowledge/lead model, new voice layer.
--
-- Run against: Supabase (PostgreSQL + pgvector)
-- Depends on:  0006_admin_and_widget_support.sql (tenants, bots, leads, handoffs)
-- ============================================================================

-- Enable uuid extension (already enabled by prior migrations, safe to repeat)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================================
-- 1. TELEPHONY CONFIG
-- ============================================================================

-- Per-tenant phone numbers (Twilio Programmable Voice numbers)
CREATE TABLE IF NOT EXISTS phone_numbers (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    bot_id          UUID REFERENCES bots(id) ON DELETE SET NULL,
    phone_number    TEXT NOT NULL,                  -- E.164 format e.g. +14432609169
    friendly_name   TEXT,
    direction       TEXT NOT NULL DEFAULT 'both',  -- 'inbound' | 'outbound' | 'both'
    provider        TEXT NOT NULL DEFAULT 'twilio',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, phone_number)
);

-- Per-bot voice profiles and IVR configuration
CREATE TABLE IF NOT EXISTS ivr_bot_configs (
    id                          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id                   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    bot_id                      UUID NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
    phone_number_id             UUID REFERENCES phone_numbers(id) ON DELETE SET NULL,

    -- TTS voice settings (ElevenLabs)
    voice_id                    TEXT,               -- ElevenLabs voice_id
    voice_model_id              TEXT DEFAULT 'eleven_flash_v2_5',
    voice_stability             FLOAT DEFAULT 0.5,
    voice_similarity_boost      FLOAT DEFAULT 0.8,
    voice_style                 FLOAT DEFAULT 0.0,

    -- STT settings (Deepgram)
    stt_model                   TEXT DEFAULT 'nova-2-phonecall',
    stt_endpointing_ms          INTEGER DEFAULT 300,
    stt_utterance_end_ms        INTEGER DEFAULT 1000,

    -- Call behavior
    call_recording_enabled      BOOLEAN DEFAULT TRUE,
    recording_disclosure_text   TEXT DEFAULT 'This call may be recorded for quality assurance purposes.',
    outbound_caller_id          TEXT,               -- E.164 number to display on outbound calls
    calling_window_start        TIME DEFAULT '09:00:00',
    calling_window_end          TIME DEFAULT '18:00:00',
    calling_timezone            TEXT DEFAULT 'America/New_York',
    max_call_attempts           INTEGER DEFAULT 3,
    retry_interval_hours        INTEGER DEFAULT 24,

    -- Grounding
    abstain_phrase              TEXT DEFAULT 'I do not have that specific information right now. Would you like me to connect you with a specialist?',
    filler_phrase               TEXT DEFAULT 'Just a moment while I look that up.',

    created_at                  TIMESTAMPTZ DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, bot_id)
);

-- ============================================================================
-- 2. CALLS
-- ============================================================================

CREATE TABLE IF NOT EXISTS calls (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    bot_id          UUID REFERENCES bots(id) ON DELETE SET NULL,
    phone_number_id UUID REFERENCES phone_numbers(id) ON DELETE SET NULL,

    -- Identity
    direction       TEXT NOT NULL,                  -- 'inbound' | 'outbound'
    from_number     TEXT NOT NULL,                  -- E.164 caller number
    to_number       TEXT NOT NULL,                  -- E.164 called number
    contact_id      UUID,                           -- FK to contacts/leads if matched

    -- Twilio identifiers
    twilio_call_sid TEXT UNIQUE,
    twilio_stream_sid TEXT,

    -- Timing
    started_at      TIMESTAMPTZ DEFAULT NOW(),
    answered_at     TIMESTAMPTZ,
    ended_at        TIMESTAMPTZ,
    duration_s      INTEGER,                        -- total call duration in seconds

    -- Outcome
    status          TEXT DEFAULT 'initiated',       -- 'initiated' | 'ringing' | 'in-progress' | 'completed' | 'failed' | 'busy' | 'no-answer'
    outcome         TEXT,                           -- 'resolved' | 'booked' | 'qualified' | 'transferred' | 'declined' | 'voicemail' | 'dropped'
    disposition     TEXT,                           -- free-text CRM disposition note

    -- AI metadata
    model_used      TEXT,                           -- primary model used on this call
    turn_count      INTEGER DEFAULT 0,
    call_summary    TEXT,                           -- compact summary for CRM / handoff briefing
    lead_id         UUID REFERENCES leads(id) ON DELETE SET NULL,
    handoff_id      UUID REFERENCES handoffs(id) ON DELETE SET NULL,

    -- Recording
    recording_url   TEXT,
    recording_sid   TEXT,
    recording_consent_given BOOLEAN DEFAULT FALSE,

    -- Cost telemetry
    telephony_minutes   FLOAT DEFAULT 0,
    stt_seconds         FLOAT DEFAULT 0,
    tts_characters      INTEGER DEFAULT 0,
    llm_input_tokens    INTEGER DEFAULT 0,
    llm_output_tokens   INTEGER DEFAULT 0,
    estimated_cost_usd  FLOAT DEFAULT 0,

    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- 3. CALL TURNS (per-utterance transcript)
-- ============================================================================

CREATE TABLE IF NOT EXISTS call_turns (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    call_id         UUID NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,

    speaker         TEXT NOT NULL,                  -- 'caller' | 'agent'
    text            TEXT NOT NULL,
    turn_index      INTEGER NOT NULL,

    -- Timing (milliseconds from call start)
    started_at_ms   INTEGER,
    ended_at_ms     INTEGER,
    latency_ms      INTEGER,                        -- STT final → TTS first chunk (agent turns only)

    -- AI metadata (agent turns only)
    model_used      TEXT,
    retrieval_mode  TEXT,                           -- 'vector' | 'fts' | 'none'
    grounded        BOOLEAN,
    tool_calls      JSONB DEFAULT '[]'::jsonb,

    -- Barge-in tracking
    was_interrupted BOOLEAN DEFAULT FALSE,          -- agent turn was cut off by caller

    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- 4. CALL TRANSCRIPTS (full assembled transcript per call)
-- ============================================================================

CREATE TABLE IF NOT EXISTS call_transcripts (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    call_id         UUID NOT NULL REFERENCES calls(id) ON DELETE CASCADE UNIQUE,
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    full_transcript TEXT NOT NULL,                  -- formatted full transcript text
    word_count      INTEGER,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- 5. OUTBOUND: CONSENT & DNC
-- ============================================================================

-- Consent records — required before any outbound dial
CREATE TABLE IF NOT EXISTS consent_records (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    phone_number    TEXT NOT NULL,                  -- E.164
    contact_id      UUID,

    consent_type    TEXT NOT NULL,                  -- 'call' | 'sms' | 'email' | 'recording'
    source          TEXT NOT NULL,                  -- 'web_form' | 'verbal_on_call' | 'crm_import' | 'chatbot'
    granted_at      TIMESTAMPTZ DEFAULT NOW(),
    revoked_at      TIMESTAMPTZ,
    is_active       BOOLEAN GENERATED ALWAYS AS (revoked_at IS NULL) STORED,

    -- Evidence
    evidence_text   TEXT,                           -- e.g. form submission URL, transcript excerpt
    call_id         UUID REFERENCES calls(id) ON DELETE SET NULL,

    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, phone_number, consent_type)
);

-- Do-not-call list
CREATE TABLE IF NOT EXISTS dnc_list (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    phone_number    TEXT NOT NULL,                  -- E.164
    added_at        TIMESTAMPTZ DEFAULT NOW(),
    source          TEXT NOT NULL DEFAULT 'self_reported',  -- 'self_reported' | 'regulatory' | 'internal' | 'verbal_request'
    reason          TEXT,
    UNIQUE(tenant_id, phone_number)
);

-- ============================================================================
-- 6. OUTBOUND CAMPAIGNS & DIAL ATTEMPTS
-- ============================================================================

CREATE TABLE IF NOT EXISTS call_campaigns (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    bot_id          UUID REFERENCES bots(id) ON DELETE SET NULL,
    name            TEXT NOT NULL,
    status          TEXT DEFAULT 'draft',           -- 'draft' | 'active' | 'paused' | 'completed'
    campaign_type   TEXT DEFAULT 'follow_up',       -- 'follow_up' | 'qualification' | 'appointment_reminder'
    call_reason     TEXT,                           -- injected into outbound prompt
    max_attempts    INTEGER DEFAULT 3,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS dial_attempts (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    campaign_id     UUID REFERENCES call_campaigns(id) ON DELETE CASCADE,
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    call_id         UUID REFERENCES calls(id) ON DELETE SET NULL,
    lead_id         UUID REFERENCES leads(id) ON DELETE CASCADE,

    phone_number    TEXT NOT NULL,
    attempt_number  INTEGER NOT NULL DEFAULT 1,
    scheduled_at    TIMESTAMPTZ,
    dialed_at       TIMESTAMPTZ,
    outcome         TEXT,                           -- 'completed' | 'voicemail' | 'no_answer' | 'busy' | 'failed' | 'consent_blocked' | 'dnc_blocked' | 'window_blocked'
    blocking_reason TEXT,                           -- why the call was blocked (if outcome = *_blocked)

    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- 7. EXTEND usage_events WITH VOICE COST FIELDS
-- ============================================================================

-- Add voice cost columns to usage_events (existing table from chatbot migrations)
ALTER TABLE usage_events
    ADD COLUMN IF NOT EXISTS telephony_minutes   FLOAT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS stt_seconds         FLOAT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS tts_characters      INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS call_id             UUID REFERENCES calls(id) ON DELETE SET NULL;

-- ============================================================================
-- 8. INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_phone_numbers_tenant    ON phone_numbers(tenant_id);
CREATE INDEX IF NOT EXISTS idx_ivr_bot_configs_tenant  ON ivr_bot_configs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_ivr_bot_configs_bot     ON ivr_bot_configs(bot_id);

CREATE INDEX IF NOT EXISTS idx_calls_tenant            ON calls(tenant_id);
CREATE INDEX IF NOT EXISTS idx_calls_started_at        ON calls(tenant_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_calls_direction         ON calls(tenant_id, direction);
CREATE INDEX IF NOT EXISTS idx_calls_outcome           ON calls(tenant_id, outcome);
CREATE INDEX IF NOT EXISTS idx_calls_twilio_sid        ON calls(twilio_call_sid) WHERE twilio_call_sid IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_call_turns_call         ON call_turns(call_id);
CREATE INDEX IF NOT EXISTS idx_call_turns_tenant       ON call_turns(tenant_id);

CREATE INDEX IF NOT EXISTS idx_consent_records_tenant  ON consent_records(tenant_id, phone_number, consent_type);
CREATE INDEX IF NOT EXISTS idx_dnc_list_tenant         ON dnc_list(tenant_id, phone_number);

CREATE INDEX IF NOT EXISTS idx_dial_attempts_campaign  ON dial_attempts(campaign_id);
CREATE INDEX IF NOT EXISTS idx_dial_attempts_tenant    ON dial_attempts(tenant_id);
CREATE INDEX IF NOT EXISTS idx_dial_attempts_lead      ON dial_attempts(lead_id);

-- ============================================================================
-- 9. ROW-LEVEL SECURITY (same pattern as existing migrations)
-- ============================================================================

ALTER TABLE phone_numbers       ENABLE ROW LEVEL SECURITY;
ALTER TABLE ivr_bot_configs     ENABLE ROW LEVEL SECURITY;
ALTER TABLE calls               ENABLE ROW LEVEL SECURITY;
ALTER TABLE call_turns          ENABLE ROW LEVEL SECURITY;
ALTER TABLE call_transcripts    ENABLE ROW LEVEL SECURITY;
ALTER TABLE consent_records     ENABLE ROW LEVEL SECURITY;
ALTER TABLE dnc_list            ENABLE ROW LEVEL SECURITY;
ALTER TABLE call_campaigns      ENABLE ROW LEVEL SECURITY;
ALTER TABLE dial_attempts       ENABLE ROW LEVEL SECURITY;

-- Tenant-scoped read/write via session variable (same as chatbot 0003 pattern)
DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'phone_numbers', 'ivr_bot_configs', 'calls', 'call_turns',
        'call_transcripts', 'consent_records', 'dnc_list',
        'call_campaigns', 'dial_attempts'
    ]
    LOOP
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I
             USING (tenant_id::text = current_setting(''app.current_tenant_id'', TRUE))
             WITH CHECK (tenant_id::text = current_setting(''app.current_tenant_id'', TRUE))',
            t
        );
    END LOOP;
END $$;

-- ============================================================================
-- 10. UPDATED_AT TRIGGERS
-- ============================================================================

CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_ivr_bot_configs_updated_at
    BEFORE UPDATE ON ivr_bot_configs
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_call_campaigns_updated_at
    BEFORE UPDATE ON call_campaigns
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ============================================================================
-- Migration complete
-- Author: Hammad Ahmed (AI/ML Engineer) | Phase 1 — AI IVR Caller
-- ============================================================================
