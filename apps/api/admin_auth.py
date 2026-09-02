"""
Admin authentication for the Next.js dashboard.

The admin UI signs in with Supabase Auth and sends the resulting access token as
a Bearer header. Every admin endpoint resolves that token to a user, then to the
tenant that user administers — so an authenticated admin still cannot read or
write another tenant's rows by passing a different tenant_id.
"""

import logging
from typing import Any, Dict

from fastapi import Depends, Header, HTTPException
from supabase import Client

from apps.api.supabase_client import get_supabase_admin
from apps.api.dependencies import get_supabase_client

logger = logging.getLogger(__name__)


async def get_current_admin(
    authorization: str = Header(None),
    supabase: Client = Depends(get_supabase_client),
) -> Dict[str, Any]:
    """
    Validates the Bearer token and returns {user_id, email, tenant_id, role}.

    Membership lives in `tenant_admins` (user_id, tenant_id, role). If a signed-in
    user has no membership row, they are authenticated but not authorized — 403.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing authentication token.")

    token = authorization.split(" ", 1)[1].strip()

    try:
        user_response = supabase.auth.get_user(token)
    except Exception as e:
        logger.warning(f"Token validation failed: {e}")
        raise HTTPException(status_code=401, detail="Invalid or expired session.")

    user = getattr(user_response, "user", None)
    if not user or not getattr(user, "id", None):
        raise HTTPException(status_code=401, detail="Invalid or expired session.")

    # Membership lookup uses the service-role client: the admin's own JWT has no
    # RLS context yet, and this table is the thing that establishes it.
    admin_db = get_supabase_admin()
    try:
        membership = admin_db.table("tenant_admins").select(
            "tenant_id, role"
        ).eq("user_id", user.id).execute()
    except Exception as e:
        logger.error(f"tenant_admins lookup failed for user '{user.id}': {e}")
        raise HTTPException(status_code=500, detail="Could not resolve admin permissions.")

    if not membership.data:
        raise HTTPException(
            status_code=403,
            detail="This account is not linked to a tenant. Contact your administrator.",
        )

    row = membership.data[0]
    return {
        "user_id": user.id,
        "email": getattr(user, "email", None),
        "tenant_id": row["tenant_id"],
        "role": row.get("role", "admin"),
    }


async def verify_bot_ownership(
    bot_id: str, tenant_id: str, supabase: Client
) -> Dict[str, Any]:
    """
    Confirms a bot belongs to the caller's tenant before any admin mutation.
    Returns the bot row; raises 404 if it belongs to someone else (404 rather
    than 403 so the response does not confirm that another tenant's bot exists).
    """
    resp = supabase.table("bots").select("*").eq("id", bot_id).eq(
        "tenant_id", tenant_id
    ).execute()

    if not resp.data:
        raise HTTPException(status_code=404, detail="Bot not found.")

    return resp.data[0]
