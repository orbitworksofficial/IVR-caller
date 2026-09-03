-- Add WITH CHECK to the tenant-isolation policies so INSERT and UPDATE work.
--
-- THE BUG
-- -------
-- The policies created in the rls_policies migration were written as:
--
--     CREATE POLICY x_isolation ON x USING (tenant_id = get_current_tenant_id());
--
-- `USING` is evaluated for rows being READ (and for the pre-image of an
-- UPDATE or DELETE). `WITH CHECK` is evaluated for rows being WRITTEN. When a
-- FOR ALL policy omits WITH CHECK, Postgres does NOT fall back to the USING
-- expression for inserts — it denies them.
--
-- The result: every read worked, and every write failed with
--   42501: new row violates row-level security policy
--
-- In production this surfaced as intermittent 500s from /chat, because the
-- very first thing a new conversation does is INSERT into `conversations`.
-- Reads succeeded, so the bot looked healthy until someone actually spoke to it.
--
-- THE FIX
-- -------
-- Recreate each policy with both clauses, so a tenant may read and write only
-- its own rows. Dropping first is required: ALTER POLICY cannot add a
-- WITH CHECK clause to a policy that was created without one.
--
-- RLS / KEY EXPECTATIONS
-- ----------------------
-- These policies apply to the anon key with app.current_tenant_id set — the
-- normal request path. service_role (SUPABASE_SERVICE_KEY) bypasses them, and
-- is used only for the pre-tenant lookups in apps/api/security.py and for the
-- admin API, which resolves its own tenant from tenant_admins first.

DO $$
DECLARE
  t TEXT;
  tenant_tables TEXT[] := ARRAY[
    'bots', 'bot_domains',
    'knowledge_sources', 'knowledge_documents', 'knowledge_chunks',
    'contacts', 'leads', 'handoffs',
    'conversations', 'messages', 'user_memories', 'usage_events'
  ];
BEGIN
  FOREACH t IN ARRAY tenant_tables LOOP
    -- Skip anything not present, so this migration is safe on partial schemas.
    IF NOT EXISTS (
      SELECT 1 FROM information_schema.tables
      WHERE table_schema = 'public' AND table_name = t
    ) THEN
      RAISE NOTICE 'Skipping % (table does not exist)', t;
      CONTINUE;
    END IF;

    EXECUTE format('DROP POLICY IF EXISTS %I ON %I', t || '_isolation', t);
    EXECUTE format(
      'CREATE POLICY %I ON %I USING (tenant_id = get_current_tenant_id()) '
      'WITH CHECK (tenant_id = get_current_tenant_id())',
      t || '_isolation', t
    );
    RAISE NOTICE 'Policy rebuilt with WITH CHECK: %', t;
  END LOOP;
END $$;

-- `tenants` is keyed on id rather than tenant_id, so it is handled separately.
DROP POLICY IF EXISTS tenants_isolation ON tenants;
CREATE POLICY tenants_isolation ON tenants
  USING (id = get_current_tenant_id())
  WITH CHECK (id = get_current_tenant_id());
