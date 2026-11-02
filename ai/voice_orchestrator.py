import asyncio
import json
import logging
import random
import time
from typing import Dict, Any, List, Optional, Callable, Awaitable, AsyncGenerator

from ai.stt_tts.turn_manager import TurnManager, VoiceSession
from ai.stt_tts.elevenlabs import ElevenLabsTTS
from ai.memory.memory_manager import MemoryManager
from ai.memory.call_memory import format_caller_history
from ai.optimization.latency_optimizer import LatencyBudgetTracker, build_voice_token_budget
from ai.tools.executor import ToolExecutor
from ai.tools.schemas import get_tools_definition
from ai.prompts.voice.templates import (
    compile_voice_prompt,
    VOICE_GREETING_TEMPLATES,
    VOICE_FILLER_PHRASES,
    get_voice_greeting
)
from ai.prompts.voice.objections import (
    detect_objection,
    get_objection_response,
    build_objection_instruction,
)
from ai.prompts.voice.script_config import VoiceScript, resolve_voice_script
from ai.rag.voice_guardrails import VoiceGuardrails, SpokenOutputFilter, TurnClassification

logger = logging.getLogger(__name__)

_VOICE_BUDGET = build_voice_token_budget()
# ~4 chars per token: keep retrieved knowledge inside the voice RAG token budget
MAX_KNOWLEDGE_CHARS = _VOICE_BUDGET["rag_tokens_max"] * 4
# Spoken answers are 1-3 sentences; a hard cap protects latency and cost.
RESPONSE_MAX_TOKENS = 200


class VoiceOrchestrator:
    """
    Central async call loop that wires together STT transcripts, RAG retrieval,
    memory context, LLM streaming, objection handling, tool execution, TTS streaming,
    barge-in, filler injection, and call logging for Phase 1 & 2.

    Phase 3 hardening: deterministic guards for prompt injection, verbal
    opt-out, AI disclosure and abstention, plus a SpokenOutputFilter between
    the LLM and TTS that blocks ungrounded numbers and anything unspeakable.
    Phase 4: all wording/behaviour comes from a per-tenant VoiceScript.
    """

    def __init__(
        self,
        provider: Any,           # LLM provider (OpenAIProvider)
        retriever: Any,          # RAG SecurityEnforcedRetrieval
        tool_executor: ToolExecutor,
        memory_manager: MemoryManager,
        turn_manager: TurnManager,
        tts: ElevenLabsTTS,
        session: VoiceSession,
        tenant_config: Dict[str, Any],
        latency_cache: Optional[Any] = None,  # VoiceSessionCache (Phase 2 latency optimizer)
        audio_sink: Optional[Any] = None,     # Callable[[bytes], Awaitable] — sends audio to Twilio gateway
        voice_script: Optional[VoiceScript] = None,   # Phase 4 tenant script (resolved if omitted)
        guardrails: Optional[VoiceGuardrails] = None,  # Phase 3 live-call guards
        history_loader: Optional[Any] = None,          # CallerHistoryLoader (long-term memory link)
    ):
        self.provider = provider
        self.retriever = retriever
        self.tool_executor = tool_executor
        self.memory_manager = memory_manager
        self.turn_manager = turn_manager
        self.tts = tts
        self.session = session
        self.tenant_config = tenant_config
        self.latency_cache = latency_cache
        self.audio_sink = audio_sink  # Gateway injects this at call time

        self.script: VoiceScript = (
            voice_script
            or tenant_config.get("voice_script_obj")
            or resolve_voice_script(
                {"voice_script": tenant_config.get("voice_script") or {}},
                {"name": tenant_config.get("business_name", "our company")},
            )
        )
        self.guardrails = guardrails or VoiceGuardrails()
        self.history_loader = history_loader
        self.model: str = tenant_config.get("call_model") or self.script.call_model

        # Internal state
        self._messages: List[Dict[str, Any]] = []
        self._call_turns: List[Dict[str, Any]] = []
        self._call_summary: str = ""
        self._objection_attempts: Dict[str, int] = {}
        self._agent_speaking: bool = False
        self._structured_memory: Dict[str, Any] = {}
        self._caller_history_block: str = ""
        self._history_task: Optional[asyncio.Task] = None
        self._opted_out: bool = False
        self._injection_attempts: int = 0
        self.guardrail_events: List[Dict[str, Any]] = []
        self.latency = LatencyBudgetTracker()

        self._turn_lock = asyncio.Lock()
        self._tts_lock = asyncio.Lock()

        # Public hooks for observability, transfers and external events
        self.on_agent_turn: Optional[Callable[[str], Awaitable[None]]] = None
        self.on_barge_in: Optional[Callable[[], Awaitable[None]]] = None
        self.on_warm_transfer: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
        self.on_appointment_booked: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
        self.on_call_blocked: Optional[Callable[[str], Awaitable[None]]] = None
        self.on_end_call_requested: Optional[Callable[[str], Awaitable[None]]] = None

    @property
    def call_turns(self) -> List[Dict[str, Any]]:
        return self._call_turns

    @property
    def latency_report(self) -> Dict[str, Any]:
        return self.latency.get_report()

    def _caller_number(self) -> str:
        return self.tenant_config.get("caller_phone") or self.tenant_config.get("caller_number") or ""

    async def start(self) -> None:
        """
        Called at call start to initialize the call, run consent checks,
        pre-fetch data, and play the initial greeting.
        """
        logger.info(f"Starting VoiceOrchestrator for call {self.session.call_id}")

        # If latency_cache provided: pre-fetch tenant config + pre-warm top-5 FAQ embeddings concurrently
        if self.latency_cache:
            try:
                if hasattr(self.latency_cache, "prefetch_tenant_config") and hasattr(self.latency_cache, "prewarm_faq_embeddings"):
                    await asyncio.gather(
                        self.latency_cache.prefetch_tenant_config(self.session.tenant_id, self.session.bot_id),
                        self.latency_cache.prewarm_faq_embeddings(self.session.tenant_id, self.session.bot_id)
                    )
                elif hasattr(self.latency_cache, "warm_up"):
                    await self.latency_cache.warm_up(self.session.tenant_id, self.session.bot_id)
            except Exception as e:
                logger.warning(f"Latency cache pre-warming failed: {e}")

        call_direction = self.tenant_config.get('call_direction', 'inbound')

        # Run consent check for outbound calls before placing the dial
        if call_direction == 'outbound':
            caller_phone = self.tenant_config.get('caller_phone', '')
            if caller_phone:
                try:
                    consent_result = await self.tool_executor.execute(
                        'check_consent',
                        {'phone_number': caller_phone, 'consent_type': 'call'}
                    )
                    if not consent_result.get('allowed', False):
                        reason = consent_result.get('reason', 'unknown')
                        logger.warning(
                            f"Consent check blocked outbound call {self.session.call_id}: {reason}"
                        )
                        if self.on_call_blocked:
                            await self.on_call_blocked(reason)
                        # Play polite termination notice so there is no dead air
                        notice = self.script.render(self.script.blocked_notice)
                        try:
                            self._agent_speaking = True
                            await self.turn_manager.on_agent_start_speaking()
                            async for chunk in self.tts.stream_text(notice):
                                if self.audio_sink:
                                    await self.audio_sink(chunk)
                        except Exception:
                            pass
                        finally:
                            self._agent_speaking = False
                            await self.turn_manager.on_agent_done_speaking()
                        return
                except Exception as e:
                    logger.warning(f"Consent check failed, blocking outbound call for safety: {e}")
                    if self.on_call_blocked:
                        await self.on_call_blocked("consent_error")
                    return
            else:
                logger.warning("Outbound call missing caller_phone in tenant_config — skipping consent check")

        # Long-term memory: look the caller up in the background so the greeting
        # is never delayed; the first turn awaits it (bounded by the loader timeout).
        caller_number = self._caller_number()
        if self.history_loader and caller_number:
            self._history_task = asyncio.create_task(self._load_caller_history(caller_number))

        # Wire TurnManager callbacks
        self.turn_manager.on_turn_complete = self._handle_turn
        self.turn_manager.on_barge_in = self._handle_barge_in
        self.turn_manager.on_filler_needed = self._handle_filler

        # Play the greeting via TTS
        business_name = self.tenant_config.get('business_name', 'our company')
        if call_direction == 'outbound':
            greeting_key = 'outbound'
        elif self.tenant_config.get('sales_mode', False):
            greeting_key = 'inbound_sales'
        else:
            greeting_key = 'inbound_support'
        greeting = get_voice_greeting(greeting_key, business_name, self.script.greetings)

        self._call_turns.append({'speaker': 'agent', 'text': greeting})
        self._messages.append({"role": "assistant", "content": greeting})

        async with self._tts_lock:
            try:
                self._agent_speaking = True
                await self.turn_manager.on_agent_start_speaking()
                async for chunk in self.tts.stream_text(greeting):
                    if not self._agent_speaking:
                        break
                    if self.audio_sink:
                        await self.audio_sink(chunk)  # Forward audio bytes to Twilio gateway
            except Exception as e:
                logger.error(f"Failed to play greeting TTS: {e}")
            finally:
                self._agent_speaking = False
                await self.turn_manager.on_agent_done_speaking()

    async def _load_caller_history(self, caller_number: str) -> None:
        try:
            history = await self.history_loader.load(
                self.session.tenant_id, caller_number, exclude_call_id=self.session.call_id
            )
        except Exception as e:
            logger.warning(f"Caller history load failed: {e}")
            return
        profile = history.get("profile") or {}
        if profile:
            self._structured_memory = {**profile, **self._structured_memory}
        self._caller_history_block = format_caller_history(history)

    async def _await_history(self) -> None:
        if self._history_task and not self._history_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(self._history_task), timeout=2.0)
            except Exception:
                pass

    # ── Deterministic speech (no LLM) ─────────────────────────────────────────

    async def _speak(self, text: str, transcript_note: Optional[str] = None) -> str:
        """Speaks fixed text through the same TTS path as LLM answers."""
        async def one_shot() -> AsyncGenerator[str, None]:
            yield text

        async with self._tts_lock:
            try:
                self._agent_speaking = True
                await self.turn_manager.on_agent_start_speaking()
                async for chunk in self.tts.stream_sentences(one_shot()):
                    if not self._agent_speaking:
                        break
                    if self.audio_sink:
                        await self.audio_sink(chunk)
            except Exception as e:
                logger.error(f"Failed to speak deterministic reply: {e}")
            finally:
                self._agent_speaking = False
                await self.turn_manager.on_agent_done_speaking()

        self._call_turns.append({'speaker': 'agent', 'text': text})
        self._messages.append({"role": "assistant", "content": text})
        if transcript_note:
            self.guardrail_events.append({"event": transcript_note, "turn": len(self._call_turns) - 1})
        if self.on_agent_turn:
            try:
                await self.on_agent_turn(text)
            except Exception as e:
                logger.warning(f"on_agent_turn callback error: {e}")
        return text

    async def _handle_turn(self, transcript: str) -> None:
        """
        Main per-turn processing pipeline. Runs RAG, updates memory, handles objections,
        executes tool calls, compiles prompt, streams LLM, and streams TTS.
        """
        async with self._turn_lock:
            turn_id = f"turn-{len(self._call_turns)}"
            turn_started = time.monotonic()
            self.latency.start_turn(turn_id)

            # 1. Add caller turn
            self._call_turns.append({'speaker': 'caller', 'text': transcript})
            self._messages.append({"role": "user", "content": transcript})

            call_direction = self.tenant_config.get('call_direction', 'inbound')
            tenant_id = self.session.tenant_id
            bot_id = self.session.bot_id
            business_name = self.tenant_config.get('business_name', '')
            business_context = self.tenant_config.get('business_context', '')

            # 2. Pre-LLM guards (Phase 3): opt-out and injection never reach the model
            objection = detect_objection(transcript, self.script.objections)
            klass: TurnClassification = self.guardrails.classify_turn(transcript, is_objection=bool(objection))

            if klass.opt_out:
                self._opted_out = True
                await self._speak(self.script.render(self.script.opt_out_ack), "opt_out_acknowledged")
                if self.on_end_call_requested:
                    try:
                        await self.on_end_call_requested("opt_out")
                    except Exception as e:
                        logger.warning(f"on_end_call_requested failed: {e}")
                return

            if klass.injection:
                self._injection_attempts += 1
                logger.warning(f"Prompt injection blocked on call {self.session.call_id}")
                await self._speak(self.script.render(self.script.injection_reply), "injection_blocked")
                return

            await self._await_history()

            # 3. Parallel fetch: RAG retrieval and Memory context
            retrieved_chunks = []
            memory_ctx = ""
            retrieval_failed = False
            try:
                # Provide fallback for retriever signature differences
                async def fetch_rag():
                    if hasattr(self.retriever, 'retrieve'):
                        return await self.retriever.retrieve(transcript, tenant_id, bot_id, top_k=_VOICE_BUDGET["rag_chunks_max"] + 1)
                    elif hasattr(self.retriever, 'retrieve_with_fallback'):
                        query_embedding = []
                        if hasattr(self.provider, 'embed'):
                            try:
                                query_embedding = await self.provider.embed(transcript)
                            except Exception as e:
                                logger.warning(f"Could not compute embedding for RAG: {e}")
                        res = await self.retriever.retrieve_with_fallback(tenant_id, transcript, query_embedding, limit=4)
                        chunks = res.get('chunks', [])
                        return [c["content"] if isinstance(c, dict) and "content" in c else str(c) for c in chunks]
                    return []

                async def fetch_memory():
                    return self.memory_manager.build_call_context_block(
                        call_summary=self._call_summary,
                        structured_memory=self._structured_memory,
                        call_direction=call_direction,
                        call_reason=self._call_reason(),
                    )

                rag_results, memory_ctx = await asyncio.gather(
                    fetch_rag(),
                    fetch_memory(),
                    return_exceptions=True
                )

                if isinstance(rag_results, list):
                    retrieved_chunks = rag_results
                if isinstance(rag_results, Exception):
                    retrieval_failed = True
                    logger.warning(f"RAG retrieval failed: {rag_results}")

                if isinstance(memory_ctx, Exception):
                    logger.warning(f"Memory context generation failed: {memory_ctx}")
                    memory_ctx = ""

            except Exception as e:
                retrieval_failed = True
                logger.warning(f"Parallel fetch failed gracefully: {e}")

            if self._caller_history_block:
                memory_ctx = "\n".join(p for p in (memory_ctx, self._caller_history_block) if p)

            retrieved_knowledge = "\n".join([str(c) for c in retrieved_chunks]) if retrieved_chunks else "No specific knowledge found."
            if len(retrieved_knowledge) > MAX_KNOWLEDGE_CHARS:
                retrieved_knowledge = retrieved_knowledge[:MAX_KNOWLEDGE_CHARS]
            turn_instructions: List[str] = []

            # 4. Objection handling — two-attempt rule (acknowledge → counter → soft close)
            if objection:
                category = objection.category
                attempt = self._objection_attempts.get(category, 0)
                if attempt <= 1:
                    turn_instructions.append(
                        build_objection_instruction(objection, attempt + 1, business_name)
                    )
                self._objection_attempts[category] = attempt + 1

            if klass.asks_if_ai:
                turn_instructions.append(
                    f"The caller asked whether they are talking to a person. Say honestly in your "
                    f"first sentence that you are an AI assistant for {business_name or 'the business'}, "
                    f"then offer to help or to connect them with a team member."
                )

            knowledge_for_prompt = retrieved_knowledge
            if turn_instructions:
                knowledge_for_prompt += "\n" + "\n".join(f"[Instruction: {i}]" for i in turn_instructions)

            # 5. Compile voice prompt
            system_prompt_text = compile_voice_prompt(
                business_name=business_name,
                business_context=business_context,
                retrieved_knowledge=knowledge_for_prompt,
                support_mode_enabled=self.tenant_config.get('support_mode', True),
                sales_mode_enabled=self.tenant_config.get('sales_mode', True),
                memory_context=memory_ctx,
                call_direction=call_direction,
                call_reason=self._call_reason(),
                abstain_phrase=self.script.render(self.script.abstain_phrase),
                extra_rules=self.script.render(self.script.extra_rules_text),
            )

            # 6. Build messages list: [system prompt] + last N turns (voice budget)
            messages_to_send: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt_text}]
            messages_to_send.extend(self._messages[-_VOICE_BUDGET["history_turns_max"]:])

            # 7. Tool execution evaluation (Phase 2 tools: transfer, book, hours, lead)
            tool_calls_executed = []
            if self.tool_executor and hasattr(self.provider, 'tool_call'):
                tool_triggers = [
                    "transfer", "human", "agent", "person", "representative", "specialist", "someone else",
                    "book", "schedule", "reschedule", "appointment", "meeting", "consultation", "calendar",
                    "hour", "open", "close", "operating", "contact", "email", "phone"
                ]
                lower_t = transcript.lower()
                should_evaluate_tools = any(term in lower_t for term in tool_triggers)

                if should_evaluate_tools:
                    try:
                        tools_def = get_tools_definition(voice_mode=True)
                        tc_res = await self.provider.tool_call(
                            messages=messages_to_send,
                            tools=tools_def,
                            model=self.model,
                            temperature=0.2,
                        )
                        raw_tcs = tc_res.get("tool_calls", [])
                        if raw_tcs:
                            assistant_msg = tc_res.get("message")
                            content_str = (getattr(assistant_msg, "content", "") or "") if assistant_msg else ""
                            messages_to_send.append({"role": "assistant", "content": content_str, "tool_calls": raw_tcs})
                            for tc in raw_tcs:
                                fn_name = tc.function.name if hasattr(tc, "function") else tc.get("function", {}).get("name")
                                fn_args_str = tc.function.arguments if hasattr(tc, "function") else tc.get("function", {}).get("arguments", "{}")
                                try:
                                    fn_args = json.loads(fn_args_str) if isinstance(fn_args_str, str) else (fn_args_str or {})
                                except Exception:
                                    fn_args = {}

                                ok, why = self.guardrails.validate_tool_arguments(fn_name, fn_args)
                                if not ok:
                                    tool_result = {"error": why}
                                else:
                                    tool_result = await self.tool_executor.execute(fn_name, fn_args)
                                tool_calls_executed.append((fn_name, tool_result))

                                # Trigger specific callbacks
                                if fn_name == "request_warm_transfer" and self.on_warm_transfer:
                                    try:
                                        if self.script.transfer_phone and isinstance(tool_result, dict):
                                            tool_result.setdefault("transfer_phone", self.script.transfer_phone)
                                        await self.on_warm_transfer(tool_result)
                                    except Exception as e:
                                        logger.warning(f"on_warm_transfer callback failed: {e}")
                                elif fn_name in ("book_appointment", "reschedule_appointment") and self.on_appointment_booked:
                                    try:
                                        await self.on_appointment_booked(tool_result)
                                    except Exception as e:
                                        logger.warning(f"on_appointment_booked callback failed: {e}")

                                tc_id = getattr(tc, "id", None) or (tc.get("id") if isinstance(tc, dict) else "call_1")
                                messages_to_send.append(ToolExecutor.build_tool_result_message(tc_id, tool_result))
                    except Exception as e:
                        logger.warning(f"Tool call evaluation failed: {e}")

            # 8. Deterministic abstention (Phase 3): no approved knowledge → never improvise
            if self.script.hard_abstain and self.guardrails.should_hard_abstain(
                klass, retrieved_chunks, tool_used=bool(tool_calls_executed), retrieval_failed=retrieval_failed
            ):
                logger.info(f"Hard abstain on call {self.session.call_id}: no grounded knowledge")
                await self._speak(self.script.render(self.script.abstain_phrase), "hard_abstain")
                self.latency.record_turn_complete(turn_id, (time.monotonic() - turn_started) * 1000)
                return

            # 9. Stream LLM response → SpokenOutputFilter → TTS
            full_response = ""
            is_streaming = getattr(self.provider, 'supports_streaming', True)
            sources = "\n".join([
                retrieved_knowledge, business_context, memory_ctx, transcript,
                " ".join(m.get("content", "") for m in self._messages if m.get("role") == "user"),
                " ".join(json.dumps(r, default=str) for _, r in tool_calls_executed),
            ])
            speech_filter = SpokenOutputFilter(
                sources=sources,
                abstain_phrase=self.script.render(self.script.abstain_phrase),
                require_ai_disclosure=klass.asks_if_ai,
                disclosure_sentence=f"I'm an AI assistant for {business_name or 'this business'}.",
                strict_numeric_grounding=self.script.strict_numeric_grounding,
            )
            first_token_at: List[float] = []

            async with self._tts_lock:
                try:
                    self._agent_speaking = True
                    await self.turn_manager.on_agent_start_speaking()

                    async def token_generator():
                        try:
                            # Primary: provider.stream()
                            if hasattr(self.provider, 'stream') and is_streaming:
                                response_gen = self.provider.stream(
                                    messages=messages_to_send,
                                    model=self.model,
                                    temperature=0.3,
                                    max_tokens=RESPONSE_MAX_TOKENS,
                                )
                                async for token in response_gen:
                                    if not first_token_at:
                                        first_token_at.append(time.monotonic())
                                    yield token
                                return

                            # Secondary: provider.generate(stream=True)
                            if is_streaming:
                                response_gen = await self.provider.generate(
                                    messages=messages_to_send,
                                    model=self.model,
                                    temperature=0.3,
                                    max_tokens=RESPONSE_MAX_TOKENS,
                                    stream=True
                                )
                                if hasattr(response_gen, '__aiter__'):
                                    async for token in response_gen:
                                        if not first_token_at:
                                            first_token_at.append(time.monotonic())
                                        yield token
                                    return
                                elif isinstance(response_gen, dict):
                                    first_token_at.append(time.monotonic())
                                    yield response_gen.get("text", "")
                                    return

                            # Fallback: non-streaming generate
                            response = await self.provider.generate(
                                messages=messages_to_send,
                                model=self.model,
                                temperature=0.3,
                                max_tokens=RESPONSE_MAX_TOKENS,
                                stream=False
                            )
                            first_token_at.append(time.monotonic())
                            yield response.get("text", "")
                        except Exception as e:
                            logger.error(f"LLM generation failed: {e}")
                            yield "I'm sorry, I'm having trouble processing that right now."

                    # Feed filtered sentences through tts.stream_sentences() → audio_sink → Twilio gateway
                    first_audio = True
                    async for audio_chunk in self.tts.stream_sentences(speech_filter.filter(token_generator())):
                        if not self._agent_speaking:
                            logger.info("Barge-in interrupted TTS stream — stopping audio.")
                            break
                        if first_audio:
                            first_audio = False
                            self.latency.record_tts_first_chunk(turn_id, (time.monotonic() - turn_started) * 1000)
                        if self.audio_sink:
                            await self.audio_sink(audio_chunk)

                except Exception as e:
                    logger.error(f"Error during turn processing: {e}")
                finally:
                    self._agent_speaking = False
                    await self.turn_manager.on_agent_done_speaking()

            full_response = speech_filter.spoken_text
            if first_token_at:
                self.latency.record_llm_first_token(turn_id, (first_token_at[0] - turn_started) * 1000)
            self.latency.record_turn_complete(turn_id, (time.monotonic() - turn_started) * 1000)
            for v in speech_filter.violations:
                self.guardrail_events.append({"event": v, "turn": len(self._call_turns)})

            # 10. Add agent turn
            if full_response:
                self._call_turns.append({'speaker': 'agent', 'text': full_response})
                # 11. Add to conversation history
                self._messages.append({"role": "assistant", "content": full_response})

            # Notify public hook if registered
            if self.on_agent_turn and full_response:
                try:
                    await self.on_agent_turn(full_response)
                except Exception as e:
                    logger.warning(f"on_agent_turn callback error: {e}")

            # 12. Periodically compress
            if len(self._call_turns) > 8:
                try:
                    old_turns = self._call_turns[:-4]
                    self._call_summary = await self.memory_manager.extract_call_summary(old_turns, self.provider)
                except Exception as e:
                    logger.warning(f"Failed to compress call summary: {e}")

    def _call_reason(self) -> str:
        return self.tenant_config.get('call_reason') or self.script.default_call_reason or ''

    async def _handle_barge_in(self) -> None:
        """
        Cancels ongoing TTS upon caller barge-in.
        """
        logger.info(f"Barge-in detected on call {self.session.call_id}")
        self.tts.cancel()
        self._agent_speaking = False
        if self.on_barge_in:
            try:
                await self.on_barge_in()
            except Exception as e:
                logger.warning(f"on_barge_in hook failed: {e}")

    async def _handle_filler(self) -> None:
        """
        Picks a random filler phrase and streams it via TTS immediately,
        guarding against overlapping speech with answer generation.
        """
        if self._agent_speaking:
            return

        filler = random.choice(self.script.filler_phrases or VOICE_FILLER_PHRASES)
        logger.info(f"Injecting filler phrase: '{filler}'")

        async with self._tts_lock:
            if self._agent_speaking:
                return
            try:
                self._agent_speaking = True
                await self.turn_manager.on_agent_start_speaking()
                async for chunk in self.tts.stream_text(filler):
                    if not self._agent_speaking:
                        break
                    if self.audio_sink:
                        await self.audio_sink(chunk)
            except Exception as e:
                logger.error(f"Failed to play filler TTS: {e}")
            finally:
                self._agent_speaking = False
                await self.turn_manager.on_agent_done_speaking()

    async def end_call(self) -> Dict[str, Any]:
        """
        Called when call hangs up or is transferred.
        Extracts summary, computes disposition, and logs it.
        """
        logger.info(f"Ending call {self.session.call_id}")

        if self._history_task and not self._history_task.done():
            self._history_task.cancel()

        summary = ""
        disposition = "resolved"

        try:
            summary = await self.memory_manager.extract_call_summary(self._call_turns, self.provider)
        except Exception as e:
            logger.warning(f"Failed to extract final summary: {e}")

        if self._opted_out:
            disposition = "declined"
        else:
            try:
                disposition = await self.memory_manager.get_call_disposition(self._call_turns, self.provider)
            except Exception as e:
                logger.warning(f"Failed to extract call disposition: {e}")

        log_args = {
            'call_id': self.session.call_id,
            'outcome': disposition,
            'disposition_note': summary,
        }
        lead_id = self._structured_memory.get("lead_id")
        if lead_id:
            log_args['lead_id'] = lead_id
        try:
            await self.tool_executor.execute('log_disposition', log_args)
        except Exception as e:
            logger.error(f"Failed to log disposition: {e}")

        # Link this call into the shared long-term memory (CRM sees chat + calls together)
        if lead_id and self.history_loader and hasattr(self.history_loader, "link_call_to_lead"):
            await self.history_loader.link_call_to_lead(self.session.tenant_id, self.session.call_id, lead_id)

        report = self.latency_report
        if report:
            logger.info(f"Call {self.session.call_id} AI latency report (ms): {report}")
        if self.guardrail_events:
            logger.info(f"Call {self.session.call_id} guardrail events: {self.guardrail_events}")

        return {
            'summary': summary,
            'disposition': disposition,
            'turn_count': self.session.turn_count,
            'latency': report,
            'guardrail_events': list(self.guardrail_events),
            'model': self.model,
        }
