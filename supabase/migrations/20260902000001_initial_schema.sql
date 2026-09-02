-- Initial multi-tenant schema for the OrbitWorks chatbot platform.
--
-- PROVENANCE
-- ----------
-- The original 0001 migration was applied to a database this repo never had
-- credentials for, and its SQL was never committed. This file reconstructs that
-- schema from authoritative sources in the repository:
--   * database/migrations/0003_rls_policies.sql — names all 13 tenant tables
--   * database/migrations/0002_add_fts_hybrid_search.sql — knowledge_chunks shape
--     and the match_knowledge_chunks return signature
--   * the implementation plan §05 — required fields per table
--   * the application code — every column read or written by seed_knowledge.py,
--     scripts/onboard_tenant.py, ai/tools/executor.py, apps/api/chat_service.py,
--     ai/prompts/config.py and ai/memory/memory_manager.py
--
-- RLS / KEY EXPECTATIONS
-- ----------------------
-- RLS is enabled on every tenant-owned table here, but the POLICIES are defined
-- in the rls_policies migration that follows — keeping table creation and access
-- rules separate mirrors the original migration order. Until that migration runs,
-- these tables are readable only by service_role.
--
-- Two callers bypass RLS, both server-side only:
--   * service_role (SUPABASE_SERVICE_KEY) — seeding, onboarding, and the
--     bot_id -> tenant_id lookup that establishes tenant context in the first
--     place. Never sent to a browser; never prefixed NEXT_PUBLIC_.
--   * Normal requests use the anon key WITH app.current_tenant_id set, and are
--     fully subject to RLS.
--
-- EMBEDDING DIMENSION
-- -------------------
-- knowledge_chunks.embedding is VECTOR(1536) to match OpenAI text-embedding-3-small,
-- which is what the hybrid search function in the next migration expects. For local
-- dev with sentence-transformers (384-dim), see the note at the end of this file.
--
-- Safe to re-run: every statement is guarded.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "vector";     -- pgvector

-- ── Tenants & bots ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tenants (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name        VARCHAR(200) NOT NULL,
  status      VARCHAR(24) DEFAULT 'active',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS bots (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id            UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name                 VARCHAR(200) NOT NULL,
  status               VARCHAR(24) DEFAULT 'active',
  welcome_message      TEXT,
  system_personality   TEXT,
  booking_url          TEXT,
  support_mode_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  sales_mode_enabled   BOOLEAN NOT NULL DEFAULT TRUE,
  handoff_settings     JSONB DEFAULT '{}'::JSONB,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bots_tenant ON bots(tenant_id);

-- bot_domains is created here so the RLS policies migration can reference it.
-- The admin/widget migration later adds bot_id and its own policy.
CREATE TABLE IF NOT EXISTS bot_domains (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  domain      VARCHAR(253) NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Knowledge base ───────────────────────────────────────────────────────────

-- Both `name` and `source_name` exist: seed_knowledge.py writes source_name
-- while scripts/onboard_tenant.py writes name. Keeping both keeps each working.
CREATE TABLE IF NOT EXISTS knowledge_sources (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  bot_id       UUID REFERENCES bots(id) ON DELETE CASCADE,
  name         VARCHAR(200),
  source_name  VARCHAR(200),
  source_type  VARCHAR(50) DEFAULT 'document',
  status       VARCHAR(24) DEFAULT 'active',
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_knowledge_sources_tenant ON knowledge_sources(tenant_id);

CREATE TABLE IF NOT EXISTS knowledge_documents (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  source_id    UUID REFERENCES knowledge_sources(id) ON DELETE CASCADE,
  title        VARCHAR(300),
  file_path    TEXT,
  status       VARCHAR(24) DEFAULT 'processed',
  metadata     JSONB DEFAULT '{}'::JSONB,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_knowledge_documents_tenant ON knowledge_documents(tenant_id);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  source_id    UUID REFERENCES knowledge_sources(id) ON DELETE CASCADE,
  document_id  UUID REFERENCES knowledge_documents(id) ON DELETE CASCADE,
  content      TEXT NOT NULL,
  embedding    VECTOR(1536),
  title        VARCHAR(300),
  category     VARCHAR(100),
  source_url   VARCHAR(500),
  metadata     JSONB DEFAULT '{}'::JSONB,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_tenant ON knowledge_chunks(tenant_id);

-- IVFFlat index for vector similarity. Created only when the table has rows —
-- building it on an empty table produces a useless index, and Postgres cannot
-- create it inside a transaction block on some versions.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE indexname = 'idx_knowledge_chunks_embedding'
  ) THEN
    BEGIN
      CREATE INDEX idx_knowledge_chunks_embedding
        ON knowledge_chunks USING ivfflat (embedding vector_cosine_ops)
        WITH (lists = 100);
    EXCEPTION WHEN OTHERS THEN
      RAISE NOTICE 'Skipped ivfflat index: %. Re-create it after seeding data.', SQLERRM;
    END;
  END IF;
END $$;

-- ── People & sales ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS contacts (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name        VARCHAR(200),
  email       VARCHAR(320),
  phone       VARCHAR(64),
  company     VARCHAR(200),
  source      VARCHAR(50) DEFAULT 'chatbot',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ToolExecutor._save_contact_details upserts on (tenant_id, email), which
-- requires this exact unique constraint to exist.
CREATE UNIQUE INDEX IF NOT EXISTS uq_contacts_tenant_email
  ON contacts(tenant_id, email) WHERE email IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_contacts_tenant ON contacts(tenant_id);

CREATE TABLE IF NOT EXISTS leads (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id             UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  contact_id            UUID REFERENCES contacts(id) ON DELETE SET NULL,
  name                  VARCHAR(200),
  email                 VARCHAR(320),
  company               VARCHAR(200),
  requirement           TEXT,
  lead_stage            VARCHAR(50) DEFAULT 'discovered',
  services_of_interest  TEXT[] DEFAULT '{}',
  source                VARCHAR(50) DEFAULT 'chatbot',
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_leads_tenant ON leads(tenant_id);

CREATE TABLE IF NOT EXISTS handoffs (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  conversation_id  UUID,
  reason           TEXT,
  summary          TEXT,
  status           VARCHAR(24) DEFAULT 'pending',
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_handoffs_tenant ON handoffs(tenant_id);

-- ── Conversations ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS conversations (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  bot_id         UUID REFERENCES bots(id) ON DELETE SET NULL,
  contact_id     UUID REFERENCES contacts(id) ON DELETE SET NULL,
  session_id     VARCHAR(128),
  status         VARCHAR(24) DEFAULT 'active',
  summary        TEXT,
  started_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_activity  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversations_tenant ON conversations(tenant_id);
CREATE INDEX IF NOT EXISTS idx_conversations_session ON conversations(session_id);

CREATE TABLE IF NOT EXISTS messages (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  conversation_id  UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role             VARCHAR(24) NOT NULL,
  content          TEXT NOT NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_created
  ON messages(conversation_id, created_at);

-- Structured long-term memory. Defined because the RLS policies migration
-- expects it; the current implementation stores memory on conversations.summary.
CREATE TABLE IF NOT EXISTS user_memories (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  contact_id  UUID REFERENCES contacts(id) ON DELETE CASCADE,
  session_id  VARCHAR(128),
  memory      JSONB DEFAULT '{}'::JSONB,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Metering ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS usage_events (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  conversation_id  UUID REFERENCES conversations(id) ON DELETE SET NULL,
  model            VARCHAR(100),
  input_tokens     INTEGER DEFAULT 0,
  output_tokens    INTEGER DEFAULT 0,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_usage_events_tenant ON usage_events(tenant_id);

-- ── Enable RLS (policies come in the next migration) ─────────────────────────

ALTER TABLE tenants             ENABLE ROW LEVEL SECURITY;
ALTER TABLE bots                ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_domains         ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_sources   ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_chunks    ENABLE ROW LEVEL SECURITY;
ALTER TABLE contacts            ENABLE ROW LEVEL SECURITY;
ALTER TABLE leads               ENABLE ROW LEVEL SECURITY;
ALTER TABLE handoffs            ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations       ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages            ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_memories       ENABLE ROW LEVEL SECURITY;
ALTER TABLE usage_events        ENABLE ROW LEVEL SECURITY;

-- ── Local-development note ───────────────────────────────────────────────────
-- To run with free local embeddings (sentence-transformers, 384-dim) instead of
-- OpenAI, resize the column and the search function's parameter:
--
--   ALTER TABLE knowledge_chunks DROP COLUMN embedding;
--   ALTER TABLE knowledge_chunks ADD COLUMN embedding VECTOR(384);
--   -- then re-create match_knowledge_chunks with VECTOR(384) in its signature
--
-- Do this BEFORE ingesting anything; switching afterwards requires a re-seed
-- because existing vectors become unreadable at the new dimension.
