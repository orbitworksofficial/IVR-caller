-- Role-based permissions and a super-admin layer.
--
-- Two concepts:
--
--   role = 'client'      sees only their own tenant. May read leads,
--                        conversations and handoffs, and manage their own
--                        knowledge base. May NOT change bot config, branding,
--                        domains, or create/delete bots.
--
--   role = 'admin'       full control within their tenant (the previous
--                        behaviour, kept for your own accounts).
--
--   is_super_admin       may switch into ANY tenant and see an overview across
--                        all of them. Independent of role, so a super-admin is
--                        still scoped to one tenant unless they explicitly
--                        switch.
--
-- RLS / KEY EXPECTATIONS
-- ----------------------
-- These columns are read by the API (apps/api/admin_auth.py) using the
-- service-role key, which bypasses RLS — the membership lookup is what
-- establishes identity, so it cannot itself run under RLS. Permission
-- enforcement therefore lives in the API layer, not in RLS: RLS guarantees
-- tenant isolation, roles govern what an authenticated admin may do inside
-- their tenant.
--
-- Safe to re-run.

-- ── 1. Role and super-admin flag ─────────────────────────────────────────────

ALTER TABLE tenant_admins
  ADD COLUMN IF NOT EXISTS is_super_admin BOOLEAN NOT NULL DEFAULT FALSE;

-- `role` already exists with a default of 'admin'. Constrain it to known values
-- so a typo cannot silently grant or remove access.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'tenant_admins_role_check'
  ) THEN
    -- Normalize anything unexpected before applying the constraint.
    UPDATE tenant_admins SET role = 'admin'
      WHERE role IS NULL OR role NOT IN ('admin', 'client');

    ALTER TABLE tenant_admins
      ADD CONSTRAINT tenant_admins_role_check
      CHECK (role IN ('admin', 'client'));
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_tenant_admins_super
  ON tenant_admins(is_super_admin) WHERE is_super_admin = TRUE;

-- ── 2. Business details on the tenant ────────────────────────────────────────
-- Captured at onboarding so a super-admin can see who a tenant actually is
-- without opening their bot config.

ALTER TABLE tenants ADD COLUMN IF NOT EXISTS contact_email  VARCHAR(320);
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS contact_phone  VARCHAR(64);
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS website        VARCHAR(500);
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS industry       VARCHAR(120);
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS notes          TEXT;

-- ── 3. Promote the first existing admin to super-admin ───────────────────────
-- Without this nobody could reach the new super-admin features, and there is
-- no way to grant the flag through the UI until someone already has it.

DO $$
DECLARE
  first_admin UUID;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM tenant_admins WHERE is_super_admin) THEN
    SELECT id INTO first_admin FROM tenant_admins ORDER BY created_at LIMIT 1;
    IF first_admin IS NOT NULL THEN
      UPDATE tenant_admins SET is_super_admin = TRUE WHERE id = first_admin;
      RAISE NOTICE 'Promoted the earliest tenant_admins row to super-admin.';
    END IF;
  END IF;
END $$;
