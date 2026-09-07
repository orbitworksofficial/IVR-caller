"""
Super-admin API — managing tenants and their accounts.

Every route here requires is_super_admin. These are the only endpoints that
read across tenant boundaries, and they use the service-role client to do so
deliberately, gated by require_super_admin rather than by RLS.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from apps.api.permissions import require_super_admin
from apps.api.supabase_client import get_supabase_admin
from apps.api.security import invalidate_bot_cache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/superadmin", tags=["Super Admin"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class TenantSummary(BaseModel):
    id: str
    name: str
    contact_email: Optional[str] = None
    industry: Optional[str] = None
    website: Optional[str] = None
    created_at: Optional[Any] = None
    bot_count: int = 0
    chunk_count: int = 0
    conversation_count: int = 0
    lead_count: int = 0
    pending_handoffs: int = 0
    admin_count: int = 0


class CreateTenantRequest(BaseModel):
    """Creates a tenant, its first bot, and the client's login in one step."""
    business_name: str = Field(..., min_length=1, max_length=200)
    admin_email: str = Field(..., min_length=3, max_length=320)
    admin_password: str = Field(..., min_length=8, max_length=200)

    # Optional extras — everything here is editable later in the bot config.
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    website: Optional[str] = None
    industry: Optional[str] = None
    notes: Optional[str] = None
    role: str = Field("client", pattern="^(admin|client)$")


class CreateTenantResponse(BaseModel):
    tenant_id: str
    bot_id: str
    user_id: str
    admin_email: str
    embed_snippet: str


class AdminAccount(BaseModel):
    id: str
    user_id: str
    tenant_id: str
    tenant_name: Optional[str] = None
    email: Optional[str] = None
    role: str
    is_super_admin: bool = False
    created_at: Optional[Any] = None


class UpdateAdminRequest(BaseModel):
    role: Optional[str] = Field(None, pattern="^(admin|client)$")
    is_super_admin: Optional[bool] = None


# ── Tenants ───────────────────────────────────────────────────────────────────

@router.get("/tenants", response_model=List[TenantSummary])
async def list_tenants(admin: Dict[str, Any] = Depends(require_super_admin)):
    """Every tenant, with per-tenant counts for the overview page."""
    db = get_supabase_admin()

    tenants = db.table("tenants").select("*").order("created_at").execute()

    def count(table: str, tenant_id: str, extra: Optional[Dict[str, Any]] = None) -> int:
        try:
            q = db.table(table).select("id", count="exact").eq("tenant_id", tenant_id)
            for k, v in (extra or {}).items():
                q = q.eq(k, v)
            return q.limit(1).execute().count or 0
        except Exception as e:
            logger.warning(f"Count failed for {table}: {e}")
            return 0

    out: List[TenantSummary] = []
    for t in (tenants.data or []):
        tid = str(t["id"])
        try:
            admins = db.table("tenant_admins").select(
                "id", count="exact"
            ).eq("tenant_id", tid).limit(1).execute().count or 0
        except Exception:
            admins = 0

        out.append(TenantSummary(
            id=tid,
            name=t.get("name") or "Unnamed",
            contact_email=t.get("contact_email"),
            industry=t.get("industry"),
            website=t.get("website"),
            created_at=t.get("created_at"),
            bot_count=count("bots", tid),
            chunk_count=count("knowledge_chunks", tid),
            conversation_count=count("conversations", tid),
            lead_count=count("leads", tid),
            pending_handoffs=count("handoffs", tid, {"status": "pending"}),
            admin_count=admins,
        ))

    return out


@router.post("/tenants", response_model=CreateTenantResponse, status_code=201)
async def create_tenant(
    payload: CreateTenantRequest,
    admin: Dict[str, Any] = Depends(require_super_admin),
):
    """
    Onboards a client: tenant + bot + login, in one call.

    Rolls back the tenant if the auth user cannot be created, so a failure
    does not leave an orphaned tenant with no way to sign in.
    """
    db = get_supabase_admin()
    name = payload.business_name.strip()

    # ── 1. Tenant ────────────────────────────────────────────────────────────
    tenant_record = {"name": name}
    for field in ("contact_email", "contact_phone", "website", "industry", "notes"):
        value = getattr(payload, field, None)
        if value:
            tenant_record[field] = value.strip()

    try:
        t = db.table("tenants").insert(tenant_record).execute()
        tenant_id = str(t.data[0]["id"])
    except Exception as e:
        logger.error(f"Tenant creation failed: {e}")
        raise HTTPException(status_code=400, detail="Could not create the tenant.")

    # ── 2. Bot, with sensible defaults the client can edit later ─────────────
    try:
        b = db.table("bots").insert({
            "tenant_id": tenant_id,
            "name": name,
            "welcome_message": f"Hi! I'm the {name} assistant. How can I help you today?",
            "system_personality": f"You are the AI assistant for {name}.",
            "support_mode_enabled": True,
            "sales_mode_enabled": True,
            "tone": "professional",
            "status": "active",
            "escalation_email": (payload.contact_email or payload.admin_email).strip(),
            "launcher_label": "Chat with us",
        }).execute()
        bot_id = str(b.data[0]["id"])
    except Exception as e:
        db.table("tenants").delete().eq("id", tenant_id).execute()
        logger.error(f"Bot creation failed, tenant rolled back: {e}")
        raise HTTPException(status_code=400, detail="Could not create the bot.")

    # ── 3. Login ─────────────────────────────────────────────────────────────
    try:
        created = db.auth.admin.create_user({
            "email": payload.admin_email.strip(),
            "password": payload.admin_password,
            "email_confirm": True,   # internal account; skip the confirmation mail
        })
        user_id = created.user.id
    except Exception as e:
        db.table("bots").delete().eq("id", bot_id).execute()
        db.table("tenants").delete().eq("id", tenant_id).execute()
        message = str(e)
        logger.error(f"User creation failed, tenant rolled back: {message}")
        if "already" in message.lower() or "registered" in message.lower():
            raise HTTPException(
                status_code=409,
                detail="That email address already has an account.",
            )
        raise HTTPException(status_code=400, detail="Could not create the login.")

    # ── 4. Link the login to the tenant ──────────────────────────────────────
    try:
        db.table("tenant_admins").insert({
            "user_id": user_id,
            "tenant_id": tenant_id,
            "role": payload.role,
            "is_super_admin": False,
        }).execute()
    except Exception as e:
        logger.error(f"Membership creation failed for tenant {tenant_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail="Account created but could not be linked to the tenant. "
                   "Contact support with tenant id " + tenant_id,
        )

    logger.info(f"Super-admin {admin.get('email')} created tenant '{name}' ({tenant_id})")

    return CreateTenantResponse(
        tenant_id=tenant_id,
        bot_id=bot_id,
        user_id=user_id,
        admin_email=payload.admin_email.strip(),
        embed_snippet=(
            f'<script src="https://chat.orb-itworks.com/widget.js"\n'
            f'        data-bot-id="{bot_id}" async></script>'
        ),
    )


@router.delete("/tenants/{tenant_id}", status_code=204)
async def delete_tenant(
    tenant_id: str,
    admin: Dict[str, Any] = Depends(require_super_admin),
):
    """
    Permanently deletes a tenant and everything belonging to it.

    Foreign keys cascade, so bots, knowledge, conversations, leads and handoffs
    all go with it. This cannot be undone.
    """
    db = get_supabase_admin()

    if tenant_id == admin.get("own_tenant_id"):
        raise HTTPException(
            status_code=400,
            detail="You cannot delete your own tenant.",
        )

    existing = db.table("tenants").select("id, name").eq("id", tenant_id).execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail="Tenant not found.")

    name = existing.data[0].get("name")
    db.table("tenants").delete().eq("id", tenant_id).execute()
    invalidate_bot_cache()
    logger.warning(f"Super-admin {admin.get('email')} DELETED tenant '{name}' ({tenant_id})")


# ── Admin accounts ────────────────────────────────────────────────────────────

@router.get("/admins", response_model=List[AdminAccount])
async def list_admins(admin: Dict[str, Any] = Depends(require_super_admin)):
    """Every admin account across all tenants, with their role."""
    db = get_supabase_admin()

    rows = db.table("tenant_admins").select("*").order("created_at").execute()
    tenants = db.table("tenants").select("id, name").execute()
    tenant_names = {str(t["id"]): t.get("name") for t in (tenants.data or [])}

    # Resolve emails from Supabase Auth in one pass rather than per row.
    emails: Dict[str, str] = {}
    try:
        page = db.auth.admin.list_users()
        users = page if isinstance(page, list) else getattr(page, "users", [])
        emails = {u.id: getattr(u, "email", None) for u in users}
    except Exception as e:
        logger.warning(f"Could not list auth users: {e}")

    return [
        AdminAccount(
            id=str(r["id"]),
            user_id=str(r["user_id"]),
            tenant_id=str(r["tenant_id"]),
            tenant_name=tenant_names.get(str(r["tenant_id"])),
            email=emails.get(str(r["user_id"])),
            role=r.get("role") or "client",
            is_super_admin=bool(r.get("is_super_admin")),
            created_at=r.get("created_at"),
        )
        for r in (rows.data or [])
    ]


@router.patch("/admins/{admin_id}")
async def update_admin(
    admin_id: str,
    payload: UpdateAdminRequest,
    admin: Dict[str, Any] = Depends(require_super_admin),
):
    """Changes an account's role, or grants/revokes super-admin."""
    db = get_supabase_admin()

    existing = db.table("tenant_admins").select("*").eq("id", admin_id).execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail="Account not found.")

    row = existing.data[0]

    # Never let the last super-admin remove their own access — that would lock
    # everyone out of tenant management with no way back except SQL.
    if payload.is_super_admin is False and row.get("is_super_admin"):
        remaining = db.table("tenant_admins").select(
            "id", count="exact"
        ).eq("is_super_admin", True).limit(1).execute().count or 0
        if remaining <= 1:
            raise HTTPException(
                status_code=400,
                detail="Cannot remove the last super-admin.",
            )

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No changes supplied.")

    resp = db.table("tenant_admins").update(updates).eq("id", admin_id).execute()
    logger.info(f"Super-admin {admin.get('email')} updated account {admin_id}: {updates}")
    return resp.data[0] if resp.data else {}


@router.delete("/admins/{admin_id}", status_code=204)
async def delete_admin(
    admin_id: str,
    admin: Dict[str, Any] = Depends(require_super_admin),
):
    """Revokes an account's access. The Supabase Auth user is left intact."""
    db = get_supabase_admin()

    existing = db.table("tenant_admins").select("*").eq("id", admin_id).execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail="Account not found.")

    if str(existing.data[0].get("user_id")) == str(admin.get("user_id")):
        raise HTTPException(
            status_code=400, detail="You cannot remove your own access."
        )

    db.table("tenant_admins").delete().eq("id", admin_id).execute()
    logger.warning(f"Super-admin {admin.get('email')} revoked account {admin_id}")
