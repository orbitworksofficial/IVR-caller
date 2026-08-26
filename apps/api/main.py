import uuid
import time
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from supabase import Client

from apps.api.schemas import ChatRequest, ChatResponse, WidgetConfigResponse, HealthResponse
from apps.api.dependencies import (
    get_supabase_client, get_ai_provider, get_retriever,
    get_guardrails, get_memory_manager
)
from apps.api.telemetry import TelemetryTracker
from ai.providers.openai import OpenAIProvider
from ai.rag.retrieval import SecurityEnforcedRetrieval
from ai.rag.guardrails import GroundingGuardrails
from ai.memory.memory_manager import MemoryManager
from ai.prompts.config import TenantConfigResolver

app = FastAPI(
    title="OrbitWorks Chatbot API",
    description="Multi-tenant AI chatbot backend for OrbitWorks",
    version="1.0.0"
)

# CORS — allow widget to embed from any domain during dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check():
    """Health check endpoint to verify the server is running."""
    return HealthResponse(
        status="healthy",
        version="1.0.0",
        service="OrbitWorks Chatbot API"
    )


@app.get("/widget/config", response_model=WidgetConfigResponse, tags=["Widget"])
async def get_widget_config(
    bot_id: str,
    supabase: Client = Depends(get_supabase_client)
):
    """
    Returns the bot configuration for the frontend widget.
    Called once on widget initialization to load branding and settings.
    """
    response = supabase.table("bots").select(
        "id, name, welcome_message, booking_url, support_mode_enabled, sales_mode_enabled"
    ).eq("id", bot_id).execute()

    if not response.data:
        raise HTTPException(status_code=404, detail=f"Bot '{bot_id}' not found.")

    bot = response.data[0]
    return WidgetConfigResponse(
        bot_id=bot["id"],
        bot_name=bot["name"],
        welcome_message=bot.get("welcome_message"),
        booking_url=bot.get("booking_url"),
        support_mode_enabled=bot.get("support_mode_enabled", True),
        sales_mode_enabled=bot.get("sales_mode_enabled", True)
    )


@app.post("/chat", response_model=ChatResponse, tags=["Chat"])
async def chat(
    request: ChatRequest,
    supabase: Client = Depends(get_supabase_client),
    provider: OpenAIProvider = Depends(get_ai_provider),
    retriever: SecurityEnforcedRetrieval = Depends(get_retriever),
    guardrails: GroundingGuardrails = Depends(get_guardrails),
    memory: MemoryManager = Depends(get_memory_manager)
):
    """
    Core chat endpoint. Executes the full RAG pipeline:
    1. Input guardrail check
    2. Embed user query
    3. Retrieve relevant knowledge chunks from Supabase (tenant-isolated)
    4. Compile system prompt with tenant config + retrieved knowledge
    5. Generate response using gpt-5.6-luna
    6. Output grounding check
    7. Persist conversation and message to database
    8. Log usage and cost telemetry
    """
    start_time = time.time()

    # 1. Input safety check
    if not guardrails.is_safe_input(request.message):
        raise HTTPException(status_code=400, detail="Message blocked by safety guardrails.")

    # 2. Get or create conversation record
    conversation_id = request.conversation_id
    if not conversation_id:
        conv_resp = supabase.table("conversations").insert({
            "tenant_id": request.tenant_id,
            "bot_id": request.bot_id,
            "session_id": request.session_id,
            "status": "active"
        }).execute()
        conversation_id = conv_resp.data[0]["id"]

    # 3. Save user message to DB
    supabase.table("messages").insert({
        "tenant_id": request.tenant_id,
        "conversation_id": conversation_id,
        "role": "user",
        "content": request.message
    }).execute()

    # 4. Embed query
    query_embedding = await provider.embed(request.message)

    # 5. Retrieve tenant-isolated knowledge chunks
    chunks = await retriever.retrieve_knowledge(
        tenant_id=request.tenant_id,
        query_text=request.message,
        query_embedding=query_embedding,
        limit=5
    )

    grounded = guardrails.meets_grounding_threshold(chunks)
    retrieved_text = "\n\n".join([c["content"] for c in chunks]) if chunks else ""

    # 6. Compile system prompt dynamically from bot config
    config_resolver = TenantConfigResolver(supabase_client=supabase)
    system_prompt = await config_resolver.resolve_prompt(
        bot_id=request.bot_id,
        retrieved_knowledge=retrieved_text
    )

    # 7. Load recent conversation history for memory context
    history_resp = supabase.table("messages").select("role, content").eq(
        "conversation_id", conversation_id
    ).order("created_at").execute()
    history = history_resp.data or []
    memory_split = memory.split_history(history)

    # 8. Build message list for LLM
    messages = [{"role": "system", "content": system_prompt}]
    messages += memory_split["recent"]
    messages.append({"role": "user", "content": request.message})

    # 9. Generate response
    result = await provider.generate(messages, model="gpt-5.6-luna")
    answer = result["text"]
    usage = result.get("usage", {})

    # 10. Output grounding verification
    source_contents = [c["content"] for c in chunks]
    output_grounded = guardrails.verify_response_grounded(answer, source_contents) if source_contents else True

    # 11. Save assistant message to DB
    supabase.table("messages").insert({
        "tenant_id": request.tenant_id,
        "conversation_id": conversation_id,
        "role": "assistant",
        "content": answer
    }).execute()

    # 12. Log telemetry
    latency_ms = int((time.time() - start_time) * 1000)
    telemetry = TelemetryTracker(supabase_client=supabase)
    await telemetry.log_usage_event(
        tenant_id=request.tenant_id,
        conversation_id=conversation_id,
        model="gpt-5.6-luna",
        input_tokens=usage.get("prompt_tokens", 0),
        output_tokens=usage.get("completion_tokens", 0),
        latency_ms=latency_ms
    )

    return ChatResponse(
        response=answer,
        conversation_id=conversation_id,
        grounded=grounded and output_grounded,
        model="gpt-5.6-luna"
    )
