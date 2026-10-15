-- Voice Gateway tables missing from the call_tables migration: voice_profiles
-- and call_recordings (implementation plan §06, telephony config + calls).
--
-- voice_profiles   — reusable per-tenant TTS voices. ivr_bot_configs points at
--                    one; its own voice_id column stays as a per-bot override
--                    for configs that predate profiles.
-- call_recordings  — one row per Twilio recording, written by the gateway's
--                    recording status callback. Audio stays in Twilio storage
--                    in Phase 1; copying to Supabase Storage + retention
--                    enforcement is Phase 3 hardening.
--
-- RLS follows the call_tables pattern. The gateway writes with service_role
-- and filters on tenant_id itself (see apps/voice_gateway/call_store.py).

CREATE TABLE IF NOT EXISTS voice_profiles (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    provider            TEXT NOT NULL DEFAULT 'elevenlabs',
    voice_id            TEXT NOT NULL,
    model_id            TEXT DEFAULT 'eleven_flash_v2_5',
    stability           FLOAT DEFAULT 0.5,
    similarity_boost    FLOAT DEFAULT 0.8,
    style               FLOAT DEFAULT 0.0,
    is_default          BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, name)
);

ALTER TABLE ivr_bot_configs
    ADD COLUMN IF NOT EXISTS voice_profile_id UUID REFERENCES voice_profiles(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS call_recordings (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    call_id         UUID NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    provider        TEXT NOT NULL DEFAULT 'twilio',
    recording_sid   TEXT UNIQUE,
    recording_url   TEXT,
    storage_path    TEXT,                       -- set once copied to Supabase Storage (Phase 3)
    duration_s      INTEGER,
    channels        INTEGER DEFAULT 2,
    status          TEXT DEFAULT 'completed',
    expires_at      TIMESTAMPTZ,                -- per-tenant retention (Phase 3)
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_voice_profiles_tenant   ON voice_profiles(tenant_id);
CREATE INDEX IF NOT EXISTS idx_call_recordings_tenant  ON call_recordings(tenant_id);
CREATE INDEX IF NOT EXISTS idx_call_recordings_call    ON call_recordings(call_id);
CREATE INDEX IF NOT EXISTS idx_phone_numbers_lookup    ON phone_numbers(phone_number) WHERE is_active;

ALTER TABLE voice_profiles  ENABLE ROW LEVEL SECURITY;
ALTER TABLE call_recordings ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON voice_profiles;
CREATE POLICY tenant_isolation ON voice_profiles
    USING (tenant_id::text = current_setting('app.current_tenant_id', TRUE))
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', TRUE));

DROP POLICY IF EXISTS tenant_isolation ON call_recordings;
CREATE POLICY tenant_isolation ON call_recordings
    USING (tenant_id::text = current_setting('app.current_tenant_id', TRUE))
    WITH CHECK (tenant_id::text = current_setting('app.current_tenant_id', TRUE));
