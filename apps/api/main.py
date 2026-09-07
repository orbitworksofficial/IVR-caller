import os
import logging
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from supabase import Client

from apps.api.schemas import (
    ChatRequest, ChatResponse, WidgetConfigResponse, HealthResponse
)
from apps.api.dependencies import (
    get_supabase_client, get_ai_provider, get_retriever,
    get_guardrails, get_memory_manager
)
from apps.api.security import (
    resolve_bot, enforce_allowed_domain, enforce_rate_limit
)
from apps.api.chat_service import ChatPipeline
from apps.api.admin_routes import router as admin_router
from apps.api.superadmin_routes import router as superadmin_router
from ai.providers.openai import OpenAIProvider
from ai.rag.retrieval import SecurityEnforcedRetrieval
from ai.rag.guardrails import GroundingGuardrails
from ai.memory.memory_manager import MemoryManager
from ai.prompts.config import TenantConfigResolver

logger = logging.getLogger(__name__)

app = FastAPI(
    title="OrbitWorks Chatbot API",
    description="Multi-tenant AI chatbot backend for OrbitWorks",
    version="1.1.0"
)

# The widget is embedded on arbitrary customer sites, so the browser preflight
# must succeed from any origin. Per-bot authorization is enforced separately
# against bot_domains inside each chat request (see security.enforce_allowed_domain),
# which is the check that actually restricts who may use a given bot.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(admin_router)
app.include_router(superadmin_router)


# ── Widget bundle ────────────────────────────────────────────────────────────
# Serves the built widget so a customer's <script src="…/widget.js"> resolves.
# In production Cloudflare caches this; here it is served directly for dev.
_WIDGET_DIST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "apps", "widget", "dist",
)


@app.get("/widget.js", tags=["Widget"], include_in_schema=False)
async def serve_widget():
    """Serves the compiled embeddable widget bundle."""
    from fastapi.responses import FileResponse

    bundle = os.path.join(_WIDGET_DIST, "widget.js")
    if not os.path.exists(bundle):
        raise HTTPException(
            status_code=404,
            detail="Widget bundle not built. Run 'npm run build' in apps/widget.",
        )

    return FileResponse(
        bundle,
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check():
    """Health check endpoint."""
    return HealthResponse(
        status="healthy",
        version="1.1.0",
        service="OrbitWorks Chatbot API"
    )


@app.get("/usage", tags=["System"])
async def get_usage_summary(
    tenant_id: str,
    limit: int = 30,
    supabase: Client = Depends(get_supabase_client),
):
    """
    Returns usage and cost summary for a tenant.
    Shows recent LLM calls, total cost, avg latency, and model distribution.
    """
    config_resolver = TenantConfigResolver(supabase_client=supabase)
    summary = await config_resolver.get_tenant_usage_summary(
        tenant_id=tenant_id, limit=limit
    )
    return {"tenant_id": tenant_id, "period": f"last_{limit}_requests", **summary}


@app.get("/widget/config", response_model=WidgetConfigResponse, tags=["Widget"])
async def get_widget_config(
    bot_id: str,
    request: Request,
    supabase: Client = Depends(get_supabase_client),
):
    """
    Returns public bot configuration for the frontend widget.
    Called once on widget initialization.

    Only non-sensitive, public fields are returned (§05) — never system
    prompts, escalation contacts, or any tenant identifier.
    """
    bot = await resolve_bot(supabase, bot_id)
    await enforce_allowed_domain(supabase, bot_id, request)

    return WidgetConfigResponse(
        bot_id=str(bot["id"]),
        bot_name=bot.get("name") or "Assistant",
        welcome_message=bot.get("welcome_message"),
        booking_url=bot.get("booking_url"),
        support_mode_enabled=bot.get("support_mode_enabled", True),
        sales_mode_enabled=bot.get("sales_mode_enabled", True),
        primary_color=bot.get("primary_color") or "#E6007E",
        secondary_color=bot.get("secondary_color") or "#1A1B3A",
        logo_url=bot.get("logo_url"),
        placement=bot.get("placement") or "bottom-right",
        launcher_label=bot.get("launcher_label"),
    )


async def _prepare_chat(
    bot_id: str,
    session_id: str,
    message: str,
    request: Request,
    supabase: Client,
    provider: OpenAIProvider,
    retriever: SecurityEnforcedRetrieval,
    guardrails: GroundingGuardrails,
    memory: MemoryManager,
) -> ChatPipeline:
    """
    Shared front-door for both chat endpoints:
      1. Resolve bot_id -> tenant_id (never trust a client-supplied tenant)
      2. Enforce the bot's allowed-domain list
      3. Enforce the per-session rate limit
      4. Run the input safety guardrail
    """
    bot = await resolve_bot(supabase, bot_id)
    await enforce_allowed_domain(supabase, bot_id, request)
    enforce_rate_limit(f"{bot_id}:{session_id}")

    if not guardrails.is_safe_input(message):
        reason = guardrails.get_injection_reason(message)
        logger.warning(f"Input blocked. Bot={bot_id} Reason={reason}")
        raise HTTPException(
            status_code=400,
            detail="I'm here to help with questions about our services.",
        )

    return ChatPipeline(
        supabase=supabase,
        provider=provider,
        retriever=retriever,
        guardrails=guardrails,
        memory=memory,
        bot=bot,
    )


@app.post("/chat", response_model=ChatResponse, tags=["Chat"])
async def chat(
    payload: ChatRequest,
    request: Request,
    supabase: Client = Depends(get_supabase_client),
    provider: OpenAIProvider = Depends(get_ai_provider),
    retriever: SecurityEnforcedRetrieval = Depends(get_retriever),
    guardrails: GroundingGuardrails = Depends(get_guardrails),
    memory: MemoryManager = Depends(get_memory_manager),
):
    """
    Core chat endpoint. Returns the complete response as JSON.

    Pipeline: resolve bot -> domain check -> rate limit -> input guardrail ->
    RLS activation -> conversation -> memory -> retrieval -> grounding/abstain ->
    prompt -> model routing -> tools -> output grounding -> persist -> telemetry.
    """
    pipeline = await _prepare_chat(
        payload.bot_id, payload.session_id, payload.message,
        request, supabase, provider, retriever, guardrails, memory,
    )

    try:
        result = await pipeline.run(
            message=payload.message,
            session_id=payload.session_id,
            conversation_id=payload.conversation_id,
        )
    except Exception as e:
        # A bare 500 shows the visitor "Something went wrong" with no clue for
        # us. Log the real cause and return a readable message instead.
        logger.exception(f"Chat failed for bot '{payload.bot_id}': {e}")
        raise HTTPException(
            status_code=503,
            detail="I'm having trouble reaching my knowledge right now. "
                   "Please try again in a moment, or email sales@orb-itworks.com.",
        )

    return ChatResponse(**result)


@app.get("/chat/stream", tags=["Chat"])
async def chat_stream(
    request: Request,
    bot_id: str,
    session_id: str,
    message: str,
    conversation_id: str = None,
    supabase: Client = Depends(get_supabase_client),
    provider: OpenAIProvider = Depends(get_ai_provider),
    retriever: SecurityEnforcedRetrieval = Depends(get_retriever),
    guardrails: GroundingGuardrails = Depends(get_guardrails),
    memory: MemoryManager = Depends(get_memory_manager),
):
    """
    Streaming chat over Server-Sent Events (§03/§08).

    A GET endpoint so the widget can use the native EventSource API. Emits
    `meta`, `token`, `done` and `error` events; see ChatPipeline.run_streaming.
    """
    if not message or not message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")
    if len(message) > 4000:
        raise HTTPException(status_code=400, detail="Message is too long.")

    pipeline = await _prepare_chat(
        bot_id, session_id, message,
        request, supabase, provider, retriever, guardrails, memory,
    )

    return StreamingResponse(
        pipeline.run_streaming(
            message=message,
            session_id=session_id,
            conversation_id=conversation_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Prevents nginx from buffering the stream and defeating streaming.
            "X-Accel-Buffering": "no",
        },
    )
