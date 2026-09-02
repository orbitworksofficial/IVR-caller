import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

_supabase_client: Client = None
_supabase_admin_client: Client = None

def get_supabase() -> Client:
    """Returns a singleton Supabase client initialized from environment variables."""
    global _supabase_client
    if _supabase_client is None:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        if not url or not key:
            raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set in environment.")
        _supabase_client = create_client(url, key)
    return _supabase_client


def get_supabase_admin() -> Client:
    """
    Returns a singleton service-role Supabase client that bypasses RLS.

    Used only for server-side operations that must read across the RLS boundary
    before a tenant context exists — resolving bot_id -> tenant_id for widget
    requests, and looking up tenant_admins membership during admin login.
    Never expose this client's key to the browser.
    """
    global _supabase_admin_client
    if _supabase_admin_client is None:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")
        if not url or not key:
            raise ValueError("SUPABASE_URL and a Supabase key must be set in environment.")
        _supabase_admin_client = create_client(url, key)
    return _supabase_admin_client
