-- Migration 0003: Row-Level Security (RLS) Policies for Multi-Tenant Isolation
-- Run this AFTER 0001 and 0002 migrations have been applied.

-- Enable RLS on all tenant-scoped tables
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE bots ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_domains ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE contacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE handoffs ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_memories ENABLE ROW LEVEL SECURITY;
ALTER TABLE usage_events ENABLE ROW LEVEL SECURITY;

-- Helper function: Extract tenant_id from JWT claim (set by API on each request)
CREATE OR REPLACE FUNCTION get_current_tenant_id()
RETURNS UUID AS $$
  SELECT NULLIF(current_setting('app.current_tenant_id', true), '')::UUID;
$$ LANGUAGE sql STABLE;

-- TENANTS: Can only see their own record
DROP POLICY IF EXISTS tenants_isolation ON tenants;
CREATE POLICY tenants_isolation ON tenants
  USING (id = get_current_tenant_id());

-- BOTS: Scoped to tenant
DROP POLICY IF EXISTS bots_isolation ON bots;
CREATE POLICY bots_isolation ON bots
  USING (tenant_id = get_current_tenant_id());

-- BOT DOMAINS: Scoped to tenant
DROP POLICY IF EXISTS bot_domains_isolation ON bot_domains;
CREATE POLICY bot_domains_isolation ON bot_domains
  USING (tenant_id = get_current_tenant_id());

-- KNOWLEDGE SOURCES: Scoped to tenant
DROP POLICY IF EXISTS knowledge_sources_isolation ON knowledge_sources;
CREATE POLICY knowledge_sources_isolation ON knowledge_sources
  USING (tenant_id = get_current_tenant_id());

-- KNOWLEDGE DOCUMENTS: Scoped to tenant
DROP POLICY IF EXISTS knowledge_documents_isolation ON knowledge_documents;
CREATE POLICY knowledge_documents_isolation ON knowledge_documents
  USING (tenant_id = get_current_tenant_id());

-- KNOWLEDGE CHUNKS: Scoped to tenant (critical — prevents cross-tenant RAG leakage)
DROP POLICY IF EXISTS knowledge_chunks_isolation ON knowledge_chunks;
CREATE POLICY knowledge_chunks_isolation ON knowledge_chunks
  USING (tenant_id = get_current_tenant_id());

-- CONTACTS: Scoped to tenant
DROP POLICY IF EXISTS contacts_isolation ON contacts;
CREATE POLICY contacts_isolation ON contacts
  USING (tenant_id = get_current_tenant_id());

-- LEADS: Scoped to tenant
DROP POLICY IF EXISTS leads_isolation ON leads;
CREATE POLICY leads_isolation ON leads
  USING (tenant_id = get_current_tenant_id());

-- HANDOFFS: Scoped to tenant
DROP POLICY IF EXISTS handoffs_isolation ON handoffs;
CREATE POLICY handoffs_isolation ON handoffs
  USING (tenant_id = get_current_tenant_id());

-- CONVERSATIONS: Scoped to tenant
DROP POLICY IF EXISTS conversations_isolation ON conversations;
CREATE POLICY conversations_isolation ON conversations
  USING (tenant_id = get_current_tenant_id());

-- MESSAGES: Scoped to tenant
DROP POLICY IF EXISTS messages_isolation ON messages;
CREATE POLICY messages_isolation ON messages
  USING (tenant_id = get_current_tenant_id());

-- USER MEMORIES: Scoped to tenant
DROP POLICY IF EXISTS user_memories_isolation ON user_memories;
CREATE POLICY user_memories_isolation ON user_memories
  USING (tenant_id = get_current_tenant_id());

-- USAGE EVENTS: Scoped to tenant
DROP POLICY IF EXISTS usage_events_isolation ON usage_events;
CREATE POLICY usage_events_isolation ON usage_events
  USING (tenant_id = get_current_tenant_id());
