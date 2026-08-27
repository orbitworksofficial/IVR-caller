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
from ai.providers.routing import ModelRouter
from ai.rag.retrieval import SecurityEnforcedRetrieval
from ai.rag.guardrails import GroundingGuardrails
from ai.memory.memory_manager import MemoryManager
from ai.prompts.config import TenantConfigResolver
from ai.tools.schemas import get_tools_definition
from ai.tools.executor import ToolExecutor

app = FastAPI(
    title="OrbitWorks Chatbot API",
    description="Multi-tenant AI chatbot backend for OrbitWorks",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check():
    """Health check endpoint."""
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
    Called once on widget initialization.
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
    Core chat endpoint — full Phase 2 pipeline:
    1.  Input guardrail check
    2.  Activate RLS for this request (tenant isolation at DB level)
    3.  Get or create conversation record
    4.  Load conversation history + build memory context
    5.  Embed user query
    6.  Hybrid RAG retrieval with FTS fallback
    7.  Compile system prompt with bot config + retrieved knowledge + memory
    8.  Route to ModelRouter → tool_call() or generate()
    9.  Execute tool calls via ToolExecutor (create_lead, handoff, etc.)
    10. Output grounding verification
    11. Persist messages to database
    12. Log telemetry
    """
    start_time = time.time()

    # ── 1. Input safety check ────────────────────────────────────────────────
    if not guardrails.is_safe_input(request.message):
        raise HTTPException(status_code=400, detail="Message blocked by safety guardrails.")

    # ── 2. Activate RLS session variable (DB-level tenant isolation) ─────────
    # This makes Supabase RLS policies on all tables enforce tenant_id correctly.
    supabase.rpc(
        "set_config",
        {"setting": "app.current_tenant_id", "value": request.tenant_id}
    ).execute()

    # ── 3. Get or create conversation ────────────────────────────────────────
    conversation_id = request.conversation_id
    if not conversation_id:
        conv_resp = supabase.table("conversations").insert({
            "tenant_id": request.tenant_id,
            "bot_id": request.bot_id,
            "session_id": request.session_id,
            "status": "active"
        }).execute()
        conversation_id = conv_resp.data[0]["id"]

    # ── 4. Load history + build memory context ───────────────────────────────
    history_resp = supabase.table("messages").select(
        "role, content"
    ).eq("conversation_id", conversation_id).order("created_at").execute()
    history = history_resp.data or []

    # Split into recent (keep) and older (summarize)
    split = memory.split_history(history)
    recent_messages = split["recent"]

    # Extract structured memory from full history for sales context
    structured_memory = {}
    conversation_summary = ""
    if history:
        structured_memory = await memory.extract_structured_memory(history, provider)
        # Summarize older turns if any
        if split["to_summarize"]:
            existing_summary_resp = supabase.table("conversations").select(
                "summary"
            ).eq("id", conversation_id).execute()
            existing_summary = ""
            if existing_summary_resp.data:
                existing_summary = existing_summary_resp.data[0].get("summary") or ""
            conversation_summary = await memory.generate_summary(
                split["to_summarize"], provider, existing_summary
            )
            # Persist updated summary
            supabase.table("conversations").update(
                {"summary": conversation_summary}
            ).eq("id", conversation_id).execute()

    memory_context = memory.build_context_block(
        structured_memory=structured_memory,
        conversation_summary=conversation_summary,
    )

    # ── 5. Save user message ─────────────────────────────────────────────────
    supabase.table("messages").insert({
        "tenant_id": request.tenant_id,
        "conversation_id": conversation_id,
        "role": "user",
        "content": request.message
    }).execute()

    # ── 6. Embed + Hybrid RAG retrieval with FTS fallback ────────────────────
    query_embedding = await provider.embed(request.message)
    retrieval_result = await retriever.retrieve_with_fallback(
        tenant_id=request.tenant_id,
        query_text=request.message,
        query_embedding=query_embedding,
        limit=5,
    )
    chunks = retrieval_result["chunks"]
    retrieval_mode = retrieval_result["retrieval_mode"]
    grounded = guardrails.meets_grounding_threshold(chunks)
    retrieved_text = "\n\n".join([c["content"] for c in chunks]) if chunks else ""

    # ── 7. Compile system prompt ─────────────────────────────────────────────
    config_resolver = TenantConfigResolver(supabase_client=supabase)
    bot_config = await config_resolver.get_bot_config(request.bot_id)

    from ai.prompts.templates import compile_system_prompt
    system_prompt = compile_system_prompt(
        business_name=bot_config.get("name", "OrbitWorks"),
        business_context=bot_config.get("system_personality", "A helpful AI assistant."),
        retrieved_knowledge=retrieved_text,
        support_mode_enabled=bot_config.get("support_mode_enabled", True),
        sales_mode_enabled=bot_config.get("sales_mode_enabled", True),
        memory_context=memory_context,
    )

    # ── 8. Build message list and route through ModelRouter ──────────────────
    router = ModelRouter(provider=provider)
    tools = get_tools_definition()

    messages = [{"role": "system", "content": system_prompt}]
    messages += recent_messages
    messages.append({"role": "user", "content": request.message})

    # Always attempt tool_call — ModelRouter routes to Terra automatically
    result = await router.tool_call(messages=messages, tools=tools)
    routing_info = result.get("routing", {})
    tool_calls = result.get("tool_calls", [])

    # ── 9. Execute any tool calls via ToolExecutor ───────────────────────────
    answer = ""
    if tool_calls:
        tool_executor = ToolExecutor(
            supabase_client=supabase,
            tenant_id=request.tenant_id,
            bot_id=request.bot_id,
        )
        # Execute each tool and inject results back into messages
        tool_messages = list(messages)
        tool_messages.append(result["message"])  # assistant message with tool_calls

        for tc in tool_calls:
            tool_result = await tool_executor.execute(
                tool_name=tc.function.name,
                arguments=__import__("json").loads(tc.function.arguments),
            )
            tool_messages.append(
                ToolExecutor.build_tool_result_message(tc.id, tool_result)
            )

        # Generate final response with tool results in context
        final_result = await router.generate(messages=tool_messages)
        answer = final_result.get("text", "")
        usage = final_result.get("usage", {})
        used_model = final_result.get("routing", {}).get("model", "gpt-5.6-luna")
    else:
        # No tool calls — use the text response directly
        answer = result["message"].content or ""
        usage = {}
        used_model = routing_info.get("model", "gpt-5.6-luna")

    # ── 10. Output grounding check ───────────────────────────────────────────
    source_contents = [c["content"] for c in chunks]
    output_grounded = (
        guardrails.verify_response_grounded(answer, source_contents)
        if source_contents else True
    )

    # ── 11. Persist assistant message ────────────────────────────────────────
    supabase.table("messages").insert({
        "tenant_id": request.tenant_id,
        "conversation_id": conversation_id,
        "role": "assistant",
        "content": answer,
    }).execute()

    # ── 12. Log telemetry ────────────────────────────────────────────────────
    latency_ms = int((time.time() - start_time) * 1000)
    telemetry = TelemetryTracker(supabase_client=supabase)
    await telemetry.log_usage_event(
        tenant_id=request.tenant_id,
        conversation_id=conversation_id,
        model=used_model,
        input_tokens=usage.get("prompt_tokens", 0),
        output_tokens=usage.get("completion_tokens", 0),
        latency_ms=latency_ms,
    )

    return ChatResponse(
        response=answer,
        conversation_id=conversation_id,
        grounded=grounded and output_grounded,
        model=used_model,
    )
