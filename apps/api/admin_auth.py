"""
Admin authentication for the Next.js dashboard.

The admin UI signs in with Supabase Auth and sends the resulting access token as
a Bearer header. Every admin endpoint resolves that token to a user, then to the
tenant they administer — so an authenticated admin still cannot read or write
another tenant's rows by passing a different tenant_id.

Super-admins may additionally switch into any tenant by sending an
X-Tenant-Id header; that is the ONLY way the resolved tenant can differ from
the caller's own membership row.
"""

import logging
from typing import Any, Dict, Optional

from fastapi import Depends, Header, HTTPException
from supabase import Client

from apps.api.supabase_client import get_supabase_admin
from apps.api.dependencies import get_supabase_client

logger = logging.getLogger(__name__)


async def get_current_admin(
    authorization: str = Header(None),
    x_tenant_id: Optional[str] = Header(None),
    supabase: Client = Depends(get_supabase_client),
) -> Dict[str, Any]:
    """
    Validates the Bearer token and returns the caller's identity:

        {user_id, email, tenant_id, role, is_super_admin,
         own_tenant_id, is_impersonating}

    `tenant_id` is the tenant this request operates on. For a normal admin it
    is always their own. For a super-admin sending X-Tenant-Id it is the tenant
    they switched into — `own_tenant_id` still records where they belong.
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
            "tenant_id, role, is_super_admin"
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
    own_tenant_id = str(row["tenant_id"])
    is_super = bool(row.get("is_super_admin"))
    role = row.get("role") or "client"

    # ── Tenant switching (super-admins only) ─────────────────────────────────
    active_tenant_id = own_tenant_id
    impersonating = False

    if x_tenant_id and x_tenant_id.strip() and x_tenant_id != own_tenant_id:
        if not is_super:
            logger.warning(
                f"{user.email} attempted to switch to tenant '{x_tenant_id}' "
                f"without super-admin rights."
            )
            raise HTTPException(status_code=403, detail="Super-admin access required.")

        # Confirm the target exists, so a typo fails clearly rather than
        # producing empty results that look like data loss.
        target = admin_db.table("tenants").select("id").eq(
            "id", x_tenant_id.strip()
        ).execute()
        if not target.data:
            raise HTTPException(status_code=404, detail="Tenant not found.")

        active_tenant_id = x_tenant_id.strip()
        impersonating = True
        # A super-admin working inside someone else's tenant gets full rights
        # there; that is the point of the feature.
        role = "admin"
        logger.info(f"Super-admin {user.email} switched into tenant {active_tenant_id}")

    return {
        "user_id": user.id,
        "email": getattr(user, "email", None),
        "tenant_id": active_tenant_id,
        "own_tenant_id": own_tenant_id,
        "role": role,
        "is_super_admin": is_super,
        "is_impersonating": impersonating,
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
