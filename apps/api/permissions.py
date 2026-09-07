"""
Role-based permissions for the admin API.

Two independent concepts:

  role            what an admin may DO inside the tenant they are working in.
                  'admin'  — full control (bots, branding, domains, knowledge)
                  'client' — read leads/conversations/handoffs, manage their own
                             knowledge base, nothing else

  is_super_admin  whether they may SWITCH into other tenants at all.

Enforcement lives here rather than in RLS: RLS guarantees a tenant can never
see another tenant's rows, while roles govern what an authenticated admin may
do within the tenant they are already entitled to.
"""

import logging
from typing import Any, Dict

from fastapi import Depends, HTTPException

from apps.api.admin_auth import get_current_admin

logger = logging.getLogger(__name__)

# What each role is permitted to do.
ROLE_PERMISSIONS: Dict[str, set] = {
    "admin": {
        "view_leads", "view_conversations", "view_handoffs", "resolve_handoffs",
        "view_knowledge", "manage_knowledge",
        "view_bots", "manage_bots", "manage_domains", "manage_branding",
    },
    "client": {
        "view_leads", "view_conversations", "view_handoffs", "resolve_handoffs",
        "view_knowledge", "manage_knowledge",
        # Deliberately excluded: manage_bots, manage_domains, manage_branding.
        # A client may see their bot's settings but not change them.
        "view_bots",
    },
}


def has_permission(admin: Dict[str, Any], permission: str) -> bool:
    """True if this admin's role grants the named permission."""
    role = (admin.get("role") or "client").lower()
    return permission in ROLE_PERMISSIONS.get(role, set())


def require(permission: str):
    """
    Dependency factory: rejects the request unless the caller's role allows it.

        @router.patch("/bots/{bot_id}")
        async def update_bot(admin = Depends(require("manage_bots"))): ...

    Returns the admin dict so the endpoint still has the caller's identity.
    """
    async def _check(admin: Dict[str, Any] = Depends(get_current_admin)) -> Dict[str, Any]:
        if not has_permission(admin, permission):
            logger.info(
                f"Denied '{permission}' for {admin.get('email')} "
                f"(role={admin.get('role')})"
            )
            raise HTTPException(
                status_code=403,
                detail="Your account does not have permission to do that. "
                       "Contact your account manager if you need access.",
            )
        return admin

    return _check


async def require_super_admin(
    admin: Dict[str, Any] = Depends(get_current_admin),
) -> Dict[str, Any]:
    """Rejects the request unless the caller is a super-admin."""
    if not admin.get("is_super_admin"):
        logger.warning(
            f"Non-super-admin {admin.get('email')} attempted a super-admin action."
        )
        raise HTTPException(status_code=403, detail="Super-admin access required.")
    return admin
