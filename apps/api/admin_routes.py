"""
Admin API — everything the Next.js dashboard talks to.

Every route depends on get_current_admin, which resolves the caller's Supabase
Auth token to the single tenant they administer. That tenant_id is then applied
to every query, so an authenticated admin cannot reach another tenant's rows
even by guessing ids.
"""

import io
import uuid
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from supabase import Client

from apps.api.admin_auth import get_current_admin, verify_bot_ownership
from apps.api.dependencies import get_supabase_admin_client, get_ai_provider
from apps.api.security import invalidate_bot_cache, _normalize_host
from apps.api.schemas import (
    BotSummary, BotCreateRequest, BotUpdateRequest,
    DomainCreateRequest, DomainResponse,
    KnowledgeIngestRequest, KnowledgeIngestResponse, KnowledgeDocumentSummary,
    ConversationSummaryResponse, ConversationDetailResponse, MessageResponse,
    LeadResponse, HandoffResponse, HandoffUpdateRequest,
    DashboardStats,
)
from ai.providers.openai import OpenAIProvider
from workers.ingestion.pipeline import KnowledgeIngestionPipeline

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["Admin"])


def _tenant(admin: Dict[str, Any]) -> str:
    return admin["tenant_id"]


# ── Session ───────────────────────────────────────────────────────────────────

@router.get("/me")
async def get_me(admin: Dict[str, Any] = Depends(get_current_admin)):
    """Returns the signed-in admin and their tenant — used to bootstrap the UI."""
    return admin


# ── Bots ──────────────────────────────────────────────────────────────────────

@router.get("/bots", response_model=List[BotSummary])
async def list_bots(
    admin: Dict[str, Any] = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase_admin_client),
):
    resp = supabase.table("bots").select("*").eq(
        "tenant_id", _tenant(admin)
    ).order("created_at", desc=True).execute()

    return [
        BotSummary(
            id=str(b["id"]),
            name=b.get("name") or "Untitled bot",
            status=b.get("status") or "active",
            welcome_message=b.get("welcome_message"),
            booking_url=b.get("booking_url"),
            support_mode_enabled=b.get("support_mode_enabled", True),
            sales_mode_enabled=b.get("sales_mode_enabled", True),
            created_at=b.get("created_at"),
        )
        for b in (resp.data or [])
    ]


@router.get("/bots/{bot_id}")
async def get_bot(
    bot_id: str,
    admin: Dict[str, Any] = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase_admin_client),
):
    """Returns the full bot record for the configuration screen."""
    return await verify_bot_ownership(bot_id, _tenant(admin), supabase)


@router.post("/bots", status_code=201)
async def create_bot(
    payload: BotCreateRequest,
    admin: Dict[str, Any] = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase_admin_client),
):
    record = payload.model_dump(exclude_none=True)
    record["tenant_id"] = _tenant(admin)
    record.setdefault("status", "active")

    try:
        resp = supabase.table("bots").insert(record).execute()
    except Exception as e:
        logger.error(f"Bot creation failed: {e}")
        raise HTTPException(status_code=400, detail="Could not create bot.")

    if not resp.data:
        raise HTTPException(status_code=400, detail="Could not create bot.")
    return resp.data[0]


@router.patch("/bots/{bot_id}")
async def update_bot(
    bot_id: str,
    payload: BotUpdateRequest,
    admin: Dict[str, Any] = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase_admin_client),
):
    await verify_bot_ownership(bot_id, _tenant(admin), supabase)

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update.")

    try:
        resp = supabase.table("bots").update(updates).eq("id", bot_id).eq(
            "tenant_id", _tenant(admin)
        ).execute()
    except Exception as e:
        logger.error(f"Bot update failed for '{bot_id}': {e}")
        raise HTTPException(status_code=400, detail="Could not update bot.")

    # The widget config cache must not serve stale branding after a save.
    invalidate_bot_cache(bot_id)
    return resp.data[0] if resp.data else {}


@router.delete("/bots/{bot_id}", status_code=204)
async def delete_bot(
    bot_id: str,
    admin: Dict[str, Any] = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase_admin_client),
):
    """Archives a bot. Conversation history is preserved for auditability (§08)."""
    await verify_bot_ownership(bot_id, _tenant(admin), supabase)
    supabase.table("bots").update({"status": "archived"}).eq("id", bot_id).eq(
        "tenant_id", _tenant(admin)
    ).execute()
    invalidate_bot_cache(bot_id)


# ── Allowed domains ───────────────────────────────────────────────────────────

@router.get("/bots/{bot_id}/domains", response_model=List[DomainResponse])
async def list_domains(
    bot_id: str,
    admin: Dict[str, Any] = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase_admin_client),
):
    await verify_bot_ownership(bot_id, _tenant(admin), supabase)
    resp = supabase.table("bot_domains").select("*").eq("bot_id", bot_id).execute()
    return [
        DomainResponse(
            id=str(d["id"]), bot_id=str(d["bot_id"]),
            domain=d["domain"], created_at=d.get("created_at"),
        )
        for d in (resp.data or [])
    ]


@router.post("/bots/{bot_id}/domains", status_code=201)
async def add_domain(
    bot_id: str,
    payload: DomainCreateRequest,
    admin: Dict[str, Any] = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase_admin_client),
):
    """
    Registers an allowed embed domain. Accepts a bare host, a full URL, or a
    '*.example.com' wildcard; everything is normalized to a hostname first.
    """
    await verify_bot_ownership(bot_id, _tenant(admin), supabase)

    raw = payload.domain.strip().lower()
    if raw.startswith("*."):
        normalized = "*." + _normalize_host(raw[2:])
        if normalized == "*.":
            raise HTTPException(status_code=400, detail="Invalid domain.")
    else:
        normalized = _normalize_host(raw)
        if not normalized or "." not in normalized:
            raise HTTPException(status_code=400, detail="Invalid domain.")

    try:
        resp = supabase.table("bot_domains").insert({
            "tenant_id": _tenant(admin),
            "bot_id": bot_id,
            "domain": normalized,
        }).execute()
    except Exception as e:
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            raise HTTPException(status_code=409, detail="Domain already registered.")
        logger.error(f"Domain insert failed: {e}")
        raise HTTPException(status_code=400, detail="Could not add domain.")

    return resp.data[0] if resp.data else {}


@router.delete("/bots/{bot_id}/domains/{domain_id}", status_code=204)
async def remove_domain(
    bot_id: str,
    domain_id: str,
    admin: Dict[str, Any] = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase_admin_client),
):
    await verify_bot_ownership(bot_id, _tenant(admin), supabase)
    supabase.table("bot_domains").delete().eq("id", domain_id).eq(
        "bot_id", bot_id
    ).execute()


# ── Knowledge base ────────────────────────────────────────────────────────────

@router.get("/knowledge", response_model=List[KnowledgeDocumentSummary])
async def list_knowledge(
    admin: Dict[str, Any] = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase_admin_client),
):
    """
    Lists ingested documents, aggregated from their chunks. The embedding column
    is deliberately excluded — it is large and the UI never displays it.
    """
    resp = supabase.table("knowledge_chunks").select(
        "document_id, title, category, source_url, created_at"
    ).eq("tenant_id", _tenant(admin)).limit(5000).execute()

    documents: Dict[str, Dict[str, Any]] = {}
    for chunk in (resp.data or []):
        doc_id = str(chunk.get("document_id") or "unknown")
        if doc_id not in documents:
            documents[doc_id] = {
                "document_id": doc_id,
                "title": chunk.get("title") or "Untitled document",
                "category": chunk.get("category"),
                "source_url": chunk.get("source_url"),
                "chunk_count": 0,
                "created_at": chunk.get("created_at"),
            }
        documents[doc_id]["chunk_count"] += 1

    return sorted(
        [KnowledgeDocumentSummary(**d) for d in documents.values()],
        key=lambda d: str(d.created_at or ""),
        reverse=True,
    )


async def _ingest_text(
    supabase: Client,
    provider: OpenAIProvider,
    tenant_id: str,
    title: str,
    content: str,
    category: Optional[str],
    source_url: Optional[str],
) -> KnowledgeIngestResponse:
    """Shared ingestion path for pasted text and uploaded files."""
    pipeline = KnowledgeIngestionPipeline(supabase_client=supabase)
    document_id = str(uuid.uuid4())
    source_id = str(uuid.uuid4())

    try:
        records = await pipeline.ingest_document(
            tenant_id=tenant_id,
            source_id=source_id,
            document_id=document_id,
            text=content,
            provider=provider,
            title=title,
            category=category or "faq",
            source_url=source_url,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Ingestion failed: {e}")
        raise HTTPException(status_code=500, detail="Ingestion failed. Check server logs.")

    return KnowledgeIngestResponse(
        document_id=document_id, title=title, chunks_created=len(records)
    )


@router.post("/knowledge", response_model=KnowledgeIngestResponse, status_code=201)
async def ingest_knowledge(
    payload: KnowledgeIngestRequest,
    admin: Dict[str, Any] = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase_admin_client),
    provider: OpenAIProvider = Depends(get_ai_provider),
):
    """Ingests pasted text into the tenant's knowledge base."""
    return await _ingest_text(
        supabase, provider, _tenant(admin),
        payload.title, payload.content, payload.category, payload.source_url,
    )


@router.post("/knowledge/upload", response_model=KnowledgeIngestResponse, status_code=201)
async def upload_knowledge(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    category: Optional[str] = Form("faq"),
    admin: Dict[str, Any] = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase_admin_client),
    provider: OpenAIProvider = Depends(get_ai_provider),
):
    """Ingests an uploaded .txt, .md or .pdf file."""
    filename = file.filename or "document"
    raw = await file.read()

    MAX_BYTES = 10 * 1024 * 1024
    if len(raw) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 10MB).")

    lowered = filename.lower()
    if lowered.endswith(".pdf"):
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(raw)) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        except Exception as e:
            logger.error(f"PDF extraction failed for '{filename}': {e}")
            raise HTTPException(status_code=400, detail="Could not read this PDF.")
    elif lowered.endswith((".txt", ".md", ".csv")):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1", errors="ignore")
    else:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Upload a .pdf, .txt, .md or .csv file.",
        )

    if not text.strip():
        raise HTTPException(status_code=400, detail="No readable text found in the file.")

    return await _ingest_text(
        supabase, provider, _tenant(admin),
        title or filename, text, category, None,
    )


@router.delete("/knowledge/{document_id}", status_code=204)
async def delete_knowledge(
    document_id: str,
    admin: Dict[str, Any] = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase_admin_client),
):
    """Removes every chunk belonging to one ingested document."""
    supabase.table("knowledge_chunks").delete().eq(
        "document_id", document_id
    ).eq("tenant_id", _tenant(admin)).execute()


# ── Conversations ─────────────────────────────────────────────────────────────

@router.get("/conversations", response_model=List[ConversationSummaryResponse])
async def list_conversations(
    limit: int = 50,
    admin: Dict[str, Any] = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase_admin_client),
):
    resp = supabase.table("conversations").select("*").eq(
        "tenant_id", _tenant(admin)
    ).order("created_at", desc=True).limit(min(limit, 200)).execute()

    return [
        ConversationSummaryResponse(
            id=str(c["id"]),
            bot_id=str(c["bot_id"]) if c.get("bot_id") else None,
            session_id=c.get("session_id"),
            status=c.get("status"),
            summary=c.get("summary"),
            created_at=c.get("created_at"),
        )
        for c in (resp.data or [])
    ]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(
    conversation_id: str,
    admin: Dict[str, Any] = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase_admin_client),
):
    """Full transcript for the conversation detail view."""
    conv_resp = supabase.table("conversations").select("*").eq(
        "id", conversation_id
    ).eq("tenant_id", _tenant(admin)).execute()

    if not conv_resp.data:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    conv = conv_resp.data[0]
    msg_resp = supabase.table("messages").select("*").eq(
        "conversation_id", conversation_id
    ).order("created_at").execute()

    return ConversationDetailResponse(
        id=str(conv["id"]),
        bot_id=str(conv["bot_id"]) if conv.get("bot_id") else None,
        session_id=conv.get("session_id"),
        status=conv.get("status"),
        summary=conv.get("summary"),
        messages=[
            MessageResponse(
                id=str(m["id"]) if m.get("id") else None,
                role=m.get("role", "assistant"),
                content=m.get("content", ""),
                created_at=m.get("created_at"),
            )
            for m in (msg_resp.data or [])
        ],
    )


# ── Leads ─────────────────────────────────────────────────────────────────────

@router.get("/leads", response_model=List[LeadResponse])
async def list_leads(
    limit: int = 100,
    admin: Dict[str, Any] = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase_admin_client),
):
    resp = supabase.table("leads").select("*").eq(
        "tenant_id", _tenant(admin)
    ).order("created_at", desc=True).limit(min(limit, 500)).execute()

    return [
        LeadResponse(
            id=str(l["id"]),
            name=l.get("name"), email=l.get("email"),
            company=l.get("company"), requirement=l.get("requirement"),
            lead_stage=l.get("lead_stage"),
            services_of_interest=l.get("services_of_interest"),
            source=l.get("source"), created_at=l.get("created_at"),
        )
        for l in (resp.data or [])
    ]


# ── Handoffs ──────────────────────────────────────────────────────────────────

@router.get("/handoffs", response_model=List[HandoffResponse])
async def list_handoffs(
    limit: int = 100,
    admin: Dict[str, Any] = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase_admin_client),
):
    resp = supabase.table("handoffs").select("*").eq(
        "tenant_id", _tenant(admin)
    ).order("created_at", desc=True).limit(min(limit, 500)).execute()

    return [
        HandoffResponse(
            id=str(h["id"]), reason=h.get("reason"), summary=h.get("summary"),
            status=h.get("status"), created_at=h.get("created_at"),
        )
        for h in (resp.data or [])
    ]


@router.patch("/handoffs/{handoff_id}")
async def update_handoff(
    handoff_id: str,
    payload: HandoffUpdateRequest,
    admin: Dict[str, Any] = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase_admin_client),
):
    resp = supabase.table("handoffs").update(
        {"status": payload.status}
    ).eq("id", handoff_id).eq("tenant_id", _tenant(admin)).execute()

    if not resp.data:
        raise HTTPException(status_code=404, detail="Handoff not found.")
    return resp.data[0]


# ── Dashboard stats ───────────────────────────────────────────────────────────

@router.get("/stats", response_model=DashboardStats)
async def get_stats(
    admin: Dict[str, Any] = Depends(get_current_admin),
    supabase: Client = Depends(get_supabase_admin_client),
):
    """
    Headline numbers for the overview screen. Uses count-only queries so this
    stays cheap as tables grow.
    """
    tenant_id = _tenant(admin)

    def _count(table: str, filters: Optional[Dict[str, Any]] = None) -> int:
        try:
            query = supabase.table(table).select("id", count="exact").eq(
                "tenant_id", tenant_id
            )
            for key, value in (filters or {}).items():
                query = query.eq(key, value)
            result = query.limit(1).execute()
            return result.count or 0
        except Exception as e:
            logger.warning(f"Count failed for '{table}': {e}")
            return 0

    from ai.prompts.config import TenantConfigResolver
    resolver = TenantConfigResolver(supabase_client=supabase)
    usage = await resolver.get_tenant_usage_summary(tenant_id=tenant_id, limit=200)

    return DashboardStats(
        total_conversations=_count("conversations"),
        total_messages=_count("messages"),
        total_leads=_count("leads"),
        pending_handoffs=_count("handoffs", {"status": "pending"}),
        total_chunks=_count("knowledge_chunks"),
        total_bots=_count("bots"),
        total_cost_usd=usage.get("total_cost_usd", 0.0),
        avg_latency_ms=usage.get("avg_latency_ms", 0),
        model_distribution=usage.get("model_distribution", {}),
    )
