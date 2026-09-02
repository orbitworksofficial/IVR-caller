-- Migration 0006: Admin dashboard + widget branding support
--
-- Adds what the Next.js admin UI and the embeddable widget need:
--   1. tenant_admins  — links a Supabase Auth user to the tenant they administer
--   2. bot branding   — colors, logo, placement for the widget (§08)
--   3. bot_domains    — ensure the allowed-domain table exists with a bot_id
--   4. usage_events   — align column names with what the API writes
--
-- Safe to re-run: every statement is guarded with IF NOT EXISTS / OR REPLACE.

-- ── 1. Tenant administrators ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tenant_admins (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  role        VARCHAR(32) NOT NULL DEFAULT 'admin',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (user_id, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_tenant_admins_user ON tenant_admins(user_id);
CREATE INDEX IF NOT EXISTS idx_tenant_admins_tenant ON tenant_admins(tenant_id);

ALTER TABLE tenant_admins ENABLE ROW LEVEL SECURITY;

-- An admin may only ever see their own membership rows.
DROP POLICY IF EXISTS tenant_admins_self ON tenant_admins;
CREATE POLICY tenant_admins_self ON tenant_admins
  USING (user_id = auth.uid());

-- ── 2. Widget branding on bots ───────────────────────────────────────────────
ALTER TABLE bots ADD COLUMN IF NOT EXISTS primary_color   VARCHAR(16)  DEFAULT '#E6007E';
ALTER TABLE bots ADD COLUMN IF NOT EXISTS secondary_color VARCHAR(16)  DEFAULT '#1A1B3A';
ALTER TABLE bots ADD COLUMN IF NOT EXISTS logo_url        TEXT;
ALTER TABLE bots ADD COLUMN IF NOT EXISTS placement       VARCHAR(24)  DEFAULT 'bottom-right';
ALTER TABLE bots ADD COLUMN IF NOT EXISTS launcher_label  VARCHAR(64);
ALTER TABLE bots ADD COLUMN IF NOT EXISTS status          VARCHAR(24)  DEFAULT 'active';

-- Phase 4 config columns referenced by TenantConfigResolver — added here so a
-- fresh database matches what ai/prompts/config.py expects to read.
ALTER TABLE bots ADD COLUMN IF NOT EXISTS tone                VARCHAR(32)  DEFAULT 'professional';
ALTER TABLE bots ADD COLUMN IF NOT EXISTS response_language   VARCHAR(48)  DEFAULT 'english';
ALTER TABLE bots ADD COLUMN IF NOT EXISTS max_response_length VARCHAR(16)  DEFAULT 'medium';
ALTER TABLE bots ADD COLUMN IF NOT EXISTS topic_restrictions  TEXT[];
ALTER TABLE bots ADD COLUMN IF NOT EXISTS escalation_email    VARCHAR(255);
ALTER TABLE bots ADD COLUMN IF NOT EXISTS escalation_phone    VARCHAR(64);
ALTER TABLE bots ADD COLUMN IF NOT EXISTS business_hours      TEXT;

-- ── 3. Allowed embed domains ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bot_domains (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  bot_id      UUID NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
  domain      VARCHAR(253) NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (bot_id, domain)
);

-- Older schemas created bot_domains without bot_id; add it if missing.
ALTER TABLE bot_domains ADD COLUMN IF NOT EXISTS bot_id UUID REFERENCES bots(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_bot_domains_bot ON bot_domains(bot_id);

-- ── 4. usage_events column alignment ─────────────────────────────────────────
-- TelemetryTracker writes `latency` + `estimated_cost`; the /usage aggregator
-- read `latency_ms` + `cost_usd`, so every cost figure came back as zero.
-- Standardize on the names the plan specifies (§05) and keep both readable.
ALTER TABLE usage_events ADD COLUMN IF NOT EXISTS latency        INTEGER;
ALTER TABLE usage_events ADD COLUMN IF NOT EXISTS estimated_cost NUMERIC(12, 6);
ALTER TABLE usage_events ADD COLUMN IF NOT EXISTS cached_tokens  INTEGER DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_usage_events_tenant_created
  ON usage_events(tenant_id, created_at DESC);

-- ── 5. Helpful indexes for admin list views ──────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_conversations_tenant_created
  ON conversations(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_conversation
  ON messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_leads_tenant_created
  ON leads(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_tenant_doc
  ON knowledge_chunks(tenant_id, document_id);
