"""
Shared chat pipeline.

Both POST /chat (JSON) and GET /chat/stream (SSE) run the same retrieval,
grounding and tool logic — only the delivery differs. Keeping the pipeline here
means the streaming path can never drift from the guardrails on the JSON path.
"""

import json
import time
import logging
import asyncio
from typing import Any, AsyncGenerator, Dict, List, Optional

from supabase import Client

from ai.providers.openai import OpenAIProvider
from ai.providers.routing import ModelRouter
from ai.rag.retrieval import SecurityEnforcedRetrieval
from ai.rag.guardrails import GroundingGuardrails
from ai.memory.memory_manager import MemoryManager
from ai.prompts.config import TenantConfigResolver
from ai.prompts.templates import compile_system_prompt
from ai.tools.schemas import get_tools_definition
from ai.tools.executor import ToolExecutor
from ai.optimization.token_optimizer import TokenOptimizer
from apps.api.telemetry import TelemetryTracker

logger = logging.getLogger(__name__)

ABSTAIN_MESSAGE = (
    "I don't have specific information about that in my knowledge base. "
    "Would you like me to connect you with our team who can help directly?"
)


class ChatPipeline:
    """
    Runs the full conversation turn. Constructed per request with an already
    resolved bot record — the tenant_id comes from that record, never from the
    client (see apps/api/security.resolve_bot).
    """

    def __init__(
        self,
        supabase: Client,
        provider: OpenAIProvider,
        retriever: SecurityEnforcedRetrieval,
        guardrails: GroundingGuardrails,
        memory: MemoryManager,
        bot: Dict[str, Any],
    ):
        self.supabase = supabase
        self.provider = provider
        self.retriever = retriever
        self.guardrails = guardrails
        self.memory = memory
        self.bot = bot
        self.bot_id = str(bot["id"])
        self.tenant_id = str(bot["tenant_id"])
        self.optimizer = TokenOptimizer()

    # ── Setup phases (shared by both delivery modes) ──────────────────────────

    def activate_rls(self) -> None:
        """
        Activates the Postgres session variable that the RLS policies read.
        The value comes from the resolved bot record, so a caller cannot select
        which tenant's policies apply to them.
        """
        try:
            self.supabase.rpc(
                "set_config",
                {"setting": "app.current_tenant_id", "value": self.tenant_id},
            ).execute()
        except Exception as e:
            # Log loudly: without this the RLS policies defined in 0003 are inert.
            logger.error(f"Failed to set RLS tenant context for '{self.tenant_id}': {e}")

    def get_or_create_conversation(
        self, conversation_id: Optional[str], session_id: str
    ) -> str:
        """Returns an existing conversation id (validated) or creates a new one."""
        if conversation_id:
            existing = self.supabase.table("conversations").select("id").eq(
                "id", conversation_id
            ).eq("tenant_id", self.tenant_id).execute()
            if existing.data:
                return conversation_id
            logger.warning(
                f"Conversation '{conversation_id}' not found for tenant "
                f"'{self.tenant_id}' — starting a new one."
            )

        resp = self.supabase.table("conversations").insert({
            "tenant_id": self.tenant_id,
            "bot_id": self.bot_id,
            "session_id": session_id,
            "status": "active",
        }).execute()
        return resp.data[0]["id"]

    def load_history(self, conversation_id: str) -> List[Dict[str, str]]:
        resp = self.supabase.table("messages").select("role, content").eq(
            "conversation_id", conversation_id
        ).order("created_at").execute()
        return resp.data or []

    def save_message(self, conversation_id: str, role: str, content: str) -> None:
        self.supabase.table("messages").insert({
            "tenant_id": self.tenant_id,
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
        }).execute()

    async def build_memory_context(
        self, conversation_id: str, history: List[Dict[str, str]]
    ) -> tuple[str, List[Dict[str, str]]]:
        """
        Splits history into recent/older, summarizes the older half, and returns
        (memory_context_block, recent_messages).
        """
        split = self.memory.split_history(history)
        recent_messages = split["recent"]

        if not history:
            return "", recent_messages

        structured_memory = await self.memory.extract_structured_memory(
            history, self.provider
        )
        conversation_summary = ""

        if split["to_summarize"]:
            existing_resp = self.supabase.table("conversations").select("summary").eq(
                "id", conversation_id
            ).execute()
            existing_summary = ""
            if existing_resp.data:
                existing_summary = existing_resp.data[0].get("summary") or ""

            conversation_summary = await self.memory.generate_summary(
                split["to_summarize"], self.provider, existing_summary
            )
            self.supabase.table("conversations").update(
                {"summary": conversation_summary}
            ).eq("id", conversation_id).execute()

        context = self.memory.build_context_block(
            structured_memory=structured_memory,
            conversation_summary=conversation_summary,
        )
        return context, recent_messages

    async def retrieve(self, message: str) -> Dict[str, Any]:
        """Embeds the query and runs hybrid retrieval with FTS fallback."""
        query_embedding = await self.provider.embed(message)
        result = await self.retriever.retrieve_with_fallback(
            tenant_id=self.tenant_id,
            query_text=message,
            query_embedding=query_embedding,
            limit=6,  # fetch extra so the compressor can prune duplicates
        )
        raw_chunks = result["chunks"]
        chunks, _ = self.optimizer.compress_knowledge(raw_chunks)
        return {"chunks": chunks, "retrieval_mode": result["retrieval_mode"]}

    async def build_messages(
        self,
        message: str,
        chunks: List[Dict[str, Any]],
        memory_context: str,
        recent_messages: List[Dict[str, str]],
    ) -> List[Dict[str, Any]]:
        """Compiles the system prompt and assembles the trimmed message list."""
        config_resolver = TenantConfigResolver(supabase_client=self.supabase)
        bot_config = await config_resolver.get_bot_config(self.bot_id)

        retrieved_text = "\n\n".join(c["content"] for c in chunks) if chunks else ""
        personality = bot_config.get("system_personality") or "A helpful AI assistant."
        personality = config_resolver._apply_config_overrides(personality, bot_config)

        system_prompt = compile_system_prompt(
            business_name=bot_config.get("name", "OrbitWorks"),
            business_context=personality,
            retrieved_knowledge=retrieved_text,
            support_mode_enabled=bot_config.get("support_mode_enabled", True),
            sales_mode_enabled=bot_config.get("sales_mode_enabled", True),
            memory_context=memory_context,
        )

        trimmed_history = self.optimizer.trim_history(recent_messages)
        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        messages += trimmed_history
        messages.append({"role": "user", "content": message})

        report = self.optimizer.build_usage_report(system_prompt, trimmed_history, chunks)
        logger.info(
            f"Token budget — system:{report['system_tokens']} "
            f"history:{report['history_tokens']} "
            f"knowledge:{report['knowledge_tokens']} "
            f"total_est:{report['total_estimated']}"
        )
        return messages

    async def run_tools(
        self, router: ModelRouter, messages: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Runs the tool-calling turn. Returns either the tool-augmented message
        list (when tools fired) or the direct text answer.
        """
        result = await router.tool_call(messages=messages, tools=get_tools_definition())
        tool_calls = result.get("tool_calls", [])

        if not tool_calls:
            return {
                "used_tools": False,
                "text": result["message"].content or "",
                "usage": result.get("usage", {}),
                "model": result.get("routing", {}).get("model", "gpt-5.6-luna"),
            }

        executor = ToolExecutor(
            supabase_client=self.supabase,
            tenant_id=self.tenant_id,
            bot_id=self.bot_id,
        )
        tool_messages = list(messages)
        assistant_msg = result["message"]
        if hasattr(assistant_msg, "model_dump"):
            dumped = assistant_msg.model_dump()
            if dumped.get("content") is None:
                dumped["content"] = ""
            tool_messages.append(dumped)
        elif isinstance(assistant_msg, dict):
            if assistant_msg.get("content") is None:
                assistant_msg["content"] = ""
            tool_messages.append(assistant_msg)
        else:
            tool_messages.append({"role": "assistant", "content": getattr(assistant_msg, "content", "") or ""})

        for tc in tool_calls:
            try:
                arguments = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                logger.warning(f"Malformed tool arguments for '{tc.function.name}'.")
                arguments = {}

            # Validate before dispatch so empty/partial calls never reach the DB.
            is_valid, reason = self.guardrails.validate_tool_arguments(
                tc.function.name, arguments
            )
            if not is_valid:
                logger.info(f"Tool '{tc.function.name}' rejected: {reason}")
                tool_result = {"error": reason}
            else:
                tool_result = await executor.execute(
                    tool_name=tc.function.name, arguments=arguments
                )

            tool_messages.append(
                ToolExecutor.build_tool_result_message(tc.id, tool_result)
            )

        return {"used_tools": True, "messages": tool_messages}

    def log_telemetry(
        self,
        conversation_id: str,
        model: str,
        usage: Dict[str, Any],
        latency_ms: int,
    ) -> None:
        """Best-effort telemetry — a logging failure must not break the reply."""
        try:
            telemetry = TelemetryTracker(supabase_client=self.supabase)
            telemetry.log_usage_event_sync(
                tenant_id=self.tenant_id,
                conversation_id=conversation_id,
                model=model,
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                latency_ms=latency_ms,
            )
        except Exception as e:
            logger.error(f"Telemetry logging failed: {e}")

    # ── Delivery mode 1: complete JSON response ───────────────────────────────

    async def run(
        self, message: str, session_id: str, conversation_id: Optional[str]
    ) -> Dict[str, Any]:
        """Executes a full turn and returns the complete response."""
        start = time.time()
        self.activate_rls()

        conversation_id = self.get_or_create_conversation(conversation_id, session_id)
        history = self.load_history(conversation_id)
        (memory_context, recent_messages), retrieval = await asyncio.gather(
            self.build_memory_context(conversation_id, history),
            self.retrieve(message),
        )

        self.save_message(conversation_id, "user", message)
        chunks = retrieval["chunks"]

        if self.guardrails.should_abstain(chunks):
            logger.info(f"Abstaining — no grounded chunks. Tenant={self.tenant_id}")
            self.save_message(conversation_id, "assistant", ABSTAIN_MESSAGE)
            return {
                "response": ABSTAIN_MESSAGE,
                "conversation_id": conversation_id,
                "grounded": False,
                "model": "abstain",
            }

        grounded = self.guardrails.meets_grounding_threshold(chunks)
        messages = await self.build_messages(
            message, chunks, memory_context, recent_messages
        )

        router = ModelRouter(provider=self.provider)
        tool_outcome = await self.run_tools(router, messages)

        if tool_outcome["used_tools"]:
            final = await router.generate(messages=tool_outcome["messages"])
            answer = final.get("text", "")
            usage = final.get("usage", {})
            used_model = final.get("routing", {}).get("model", "gpt-5.6-luna")
        else:
            answer = tool_outcome["text"]
            usage = tool_outcome["usage"]
            used_model = tool_outcome["model"]

        source_contents = [c["content"] for c in chunks]
        output_grounded = (
            self.guardrails.verify_response_grounded(answer, source_contents)
            if source_contents else True
        )

        self.save_message(conversation_id, "assistant", answer)
        self.log_telemetry(
            conversation_id, used_model, usage, int((time.time() - start) * 1000)
        )

        return {
            "response": answer,
            "conversation_id": conversation_id,
            "grounded": grounded and output_grounded,
            "model": used_model,
        }

    # ── Delivery mode 2: SSE token stream ─────────────────────────────────────

    async def run_streaming(
        self, message: str, session_id: str, conversation_id: Optional[str]
    ) -> AsyncGenerator[str, None]:
        """
        Yields Server-Sent Events for the widget (§03/§08).

        Event types:
          meta   — conversation_id, sent first so the client can persist it
          token  — one chunk of assistant text
          done   — final metadata (grounded, model)
          error  — something failed; message is safe to display
        """
        start = time.time()

        def sse(event: str, data: Dict[str, Any]) -> str:
            return f"event: {event}\ndata: {json.dumps(data)}\n\n"

        try:
            self.activate_rls()
            conversation_id = self.get_or_create_conversation(conversation_id, session_id)
            yield sse("meta", {"conversation_id": conversation_id})

            history = self.load_history(conversation_id)
            (memory_context, recent_messages), retrieval = await asyncio.gather(
                self.build_memory_context(conversation_id, history),
                self.retrieve(message),
            )
            self.save_message(conversation_id, "user", message)
            chunks = retrieval["chunks"]

            if self.guardrails.should_abstain(chunks):
                logger.info(f"Abstaining — no grounded chunks. Tenant={self.tenant_id}")
                self.save_message(conversation_id, "assistant", ABSTAIN_MESSAGE)
                yield sse("token", {"text": ABSTAIN_MESSAGE})
                yield sse("done", {
                    "grounded": False,
                    "model": "abstain",
                    "conversation_id": conversation_id,
                })
                return

            grounded = self.guardrails.meets_grounding_threshold(chunks)
            messages = await self.build_messages(
                message, chunks, memory_context, recent_messages
            )

            router = ModelRouter(provider=self.provider)

            # Tool decision happens before streaming: a tool call needs the full
            # structured response, so we resolve tools first, then stream the
            # final answer with the tool results already in context.
            tool_outcome = await self.run_tools(router, messages)
            stream_messages = (
                tool_outcome["messages"] if tool_outcome["used_tools"] else messages
            )

            if not tool_outcome["used_tools"] and tool_outcome["text"]:
                # The model answered directly during the tool turn. Replay that
                # text as tokens rather than paying for a second generation.
                answer = tool_outcome["text"]
                for i in range(0, len(answer), 24):
                    yield sse("token", {"text": answer[i:i + 24]})
                used_model = tool_outcome["model"]
                usage = tool_outcome["usage"]
            else:
                answer = ""
                model_name, _ = router.determine_route(stream_messages)
                async for token in self.provider.stream(
                    messages=stream_messages, model=model_name
                ):
                    answer += token
                    yield sse("token", {"text": token})
                used_model = model_name
                # The streaming API does not return a usage block; estimate from
                # the optimizer so cost telemetry still records something real.
                usage = {
                    "prompt_tokens": self.optimizer.count_messages_tokens(stream_messages),
                    "completion_tokens": self.optimizer.count_tokens(answer),
                }

            source_contents = [c["content"] for c in chunks]
            output_grounded = (
                self.guardrails.verify_response_grounded(answer, source_contents)
                if source_contents else True
            )

            self.save_message(conversation_id, "assistant", answer)
            self.log_telemetry(
                conversation_id, used_model, usage, int((time.time() - start) * 1000)
            )

            yield sse("done", {
                "grounded": grounded and output_grounded,
                "model": used_model,
                "conversation_id": conversation_id,
            })

        except Exception as e:
            logger.exception(f"Streaming chat failed: {e}")
            yield sse("error", {
                "message": "Sorry, something went wrong. Please try again.",
            })
