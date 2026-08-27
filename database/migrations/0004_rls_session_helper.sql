-- Migration 0004: RLS Session Variable Helper
-- Exposes a safe RPC for the API to set the tenant context per request,
-- which activates the RLS policies defined in 0003_rls_policies.sql.
-- Without this, the policies in 0003 are defined but never triggered.

-- Wrap set_config in a security-definer function so the API role can call it
CREATE OR REPLACE FUNCTION set_config(setting TEXT, value TEXT)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
  PERFORM set_config(setting, value, false); -- false = not local to transaction only
END;
$$;

-- Grant execute to the authenticated and anon roles used by the API
GRANT EXECUTE ON FUNCTION set_config(TEXT, TEXT) TO authenticated;
GRANT EXECUTE ON FUNCTION set_config(TEXT, TEXT) TO anon;
GRANT EXECUTE ON FUNCTION set_config(TEXT, TEXT) TO service_role;
