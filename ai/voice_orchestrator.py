import asyncio
import json
import logging
import random
from typing import Dict, Any, List, Optional, Callable, Awaitable

from ai.stt_tts.turn_manager import TurnManager, VoiceSession
from ai.stt_tts.elevenlabs import ElevenLabsTTS
from ai.memory.memory_manager import MemoryManager
from ai.tools.executor import ToolExecutor
from ai.tools.schemas import get_tools_definition
from ai.prompts.voice.templates import (
    compile_voice_prompt,
    VOICE_GREETING_TEMPLATES,
    VOICE_FILLER_PHRASES,
    get_voice_greeting
)
from ai.prompts.voice.objections import detect_objection, get_objection_response

logger = logging.getLogger(__name__)

class VoiceOrchestrator:
    """
    Central async call loop that wires together STT transcripts, RAG retrieval,
    memory context, LLM streaming, objection handling, tool execution, TTS streaming,
    barge-in, filler injection, and call logging for Phase 1 & 2.
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

        # Internal state
        self._messages: List[Dict[str, Any]] = []
        self._call_turns: List[Dict[str, Any]] = []
        self._call_summary: str = ""
        self._objection_attempts: Dict[str, int] = {}
        self._agent_speaking: bool = False
        self._structured_memory: Dict[str, Any] = {}

        self._turn_lock = asyncio.Lock()
        self._tts_lock = asyncio.Lock()

        # Public hooks for observability, transfers and external events
        self.on_agent_turn: Optional[Callable[[str], Awaitable[None]]] = None
        self.on_barge_in: Optional[Callable[[], Awaitable[None]]] = None
        self.on_warm_transfer: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
        self.on_appointment_booked: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
        self.on_call_blocked: Optional[Callable[[str], Awaitable[None]]] = None

    @property
    def call_turns(self) -> List[Dict[str, Any]]:
        return self._call_turns

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
                        notice = "I'm sorry, we cannot connect this call at this time. Goodbye."
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

        # Wire TurnManager callbacks
        self.turn_manager.on_turn_complete = self._handle_turn
        self.turn_manager.on_barge_in = self._handle_barge_in
        self.turn_manager.on_filler_needed = self._handle_filler

        # Play the greeting via TTS
        business_name = self.tenant_config.get('business_name', 'our company')
        if call_direction == 'outbound':
            greeting = get_voice_greeting('outbound', business_name)
        elif self.tenant_config.get('sales_mode', False):
            greeting = get_voice_greeting('inbound_sales', business_name)
        else:
            greeting = get_voice_greeting('inbound_support', business_name)
        
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

    async def _handle_turn(self, transcript: str) -> None:
        """
        Main per-turn processing pipeline. Runs RAG, updates memory, handles objections,
        executes tool calls, compiles prompt, streams LLM, and streams TTS.
        """
        async with self._turn_lock:
            # 1. Add caller turn
            self._call_turns.append({'speaker': 'caller', 'text': transcript})
            self._messages.append({"role": "user", "content": transcript})

            call_direction = self.tenant_config.get('call_direction', 'inbound')
            tenant_id = self.session.tenant_id
            bot_id = self.session.bot_id
            business_name = self.tenant_config.get('business_name', '')
            business_context = self.tenant_config.get('business_context', '')

            # 2. Parallel fetch: RAG retrieval and Memory context
            retrieved_chunks = []
            memory_ctx = ""
            try:
                # Provide fallback for retriever signature differences
                async def fetch_rag():
                    if hasattr(self.retriever, 'retrieve'):
                        return await self.retriever.retrieve(transcript, tenant_id, bot_id, top_k=4)
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
                        call_direction=call_direction
                    )

                rag_results, memory_ctx = await asyncio.gather(
                    fetch_rag(),
                    fetch_memory(),
                    return_exceptions=True
                )

                if isinstance(rag_results, list):
                    retrieved_chunks = rag_results
                if isinstance(rag_results, Exception):
                    logger.warning(f"RAG retrieval failed: {rag_results}")

                if isinstance(memory_ctx, Exception):
                    logger.warning(f"Memory context generation failed: {memory_ctx}")
                    memory_ctx = ""

            except Exception as e:
                logger.warning(f"Parallel fetch failed gracefully: {e}")

            retrieved_knowledge = "\n".join([str(c) for c in retrieved_chunks]) if retrieved_chunks else "No specific knowledge found."

            # 3. Objection detection
            objection = detect_objection(transcript)
            if objection:
                category = objection.category
                attempt = self._objection_attempts.get(category, 0)
                
                if attempt == 0:
                    instruction = get_objection_response(objection, attempt_number=1)
                    retrieved_knowledge += f"\n[Instruction: {instruction}]"
                elif attempt == 1:
                    instruction = get_objection_response(objection, attempt_number=2)
                    retrieved_knowledge += f"\n[Instruction (Soft Close): {instruction}]"
                
                self._objection_attempts[category] = attempt + 1

            # 4. Compile voice prompt
            system_prompt_text = compile_voice_prompt(
                business_name=business_name,
                business_context=business_context,
                retrieved_knowledge=retrieved_knowledge,
                support_mode_enabled=self.tenant_config.get('support_mode', True),
                sales_mode_enabled=self.tenant_config.get('sales_mode', True),
                memory_context=memory_ctx,
                call_direction=call_direction,
                call_reason=self.tenant_config.get('call_reason', '')
            )

            # 5. Build messages list: [system prompt] + last 6 turns
            messages_to_send: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt_text}]
            messages_to_send.extend(self._messages[-6:])

            # 6. Tool execution evaluation (Phase 2 tools: transfer, book, hours, lead)
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
                            model="gpt-5.6-luna",
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
                                
                                tool_result = await self.tool_executor.execute(fn_name, fn_args)
                                tool_calls_executed.append((fn_name, tool_result))

                                # Trigger specific callbacks
                                if fn_name == "request_warm_transfer" and self.on_warm_transfer:
                                    try:
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

            # 7. Stream LLM response
            full_response = ""
            is_streaming = getattr(self.provider, 'supports_streaming', True)

            async with self._tts_lock:
                try:
                    self._agent_speaking = True
                    await self.turn_manager.on_agent_start_speaking()

                    async def token_generator():
                        nonlocal full_response
                        try:
                            # Primary: provider.stream()
                            if hasattr(self.provider, 'stream') and is_streaming:
                                response_gen = self.provider.stream(
                                    messages=messages_to_send,
                                    model="gpt-5.6-luna",
                                    temperature=0.3
                                )
                                async for token in response_gen:
                                    full_response += token
                                    yield token
                                return

                            # Secondary: provider.generate(stream=True)
                            if is_streaming:
                                response_gen = await self.provider.generate(
                                    messages=messages_to_send,
                                    model="gpt-5.6-luna",
                                    temperature=0.3,
                                    stream=True
                                )
                                if hasattr(response_gen, '__aiter__'):
                                    async for token in response_gen:
                                        full_response += token
                                        yield token
                                    return
                                elif isinstance(response_gen, dict):
                                    text = response_gen.get("text", "")
                                    full_response = text
                                    yield text
                                    return
                            
                            # Fallback: non-streaming generate
                            response = await self.provider.generate(
                                messages=messages_to_send,
                                model="gpt-5.6-luna",
                                temperature=0.3,
                                stream=False
                            )
                            text = response.get("text", "")
                            full_response = text
                            yield text
                        except Exception as e:
                            logger.error(f"LLM generation failed: {e}")
                            fallback = "I'm sorry, I'm having trouble processing that right now."
                            full_response = fallback
                            yield fallback

                    # Feed LLM output through tts.stream_sentences() → audio_sink → Twilio gateway
                    async for audio_chunk in self.tts.stream_sentences(token_generator()):
                        if not self._agent_speaking:
                            logger.info("Barge-in interrupted TTS stream — stopping audio.")
                            break
                        if self.audio_sink:
                            await self.audio_sink(audio_chunk)

                except Exception as e:
                    logger.error(f"Error during turn processing: {e}")
                finally:
                    self._agent_speaking = False
                    await self.turn_manager.on_agent_done_speaking()

            # 8. Add agent turn
            if full_response:
                self._call_turns.append({'speaker': 'agent', 'text': full_response})
                # 9. Add to conversation history
                self._messages.append({"role": "assistant", "content": full_response})

            # Notify public hook if registered
            if self.on_agent_turn and full_response:
                try:
                    await self.on_agent_turn(full_response)
                except Exception as e:
                    logger.warning(f"on_agent_turn callback error: {e}")

            # 10. Periodically compress
            if len(self._call_turns) > 8:
                try:
                    old_turns = self._call_turns[:-4]
                    self._call_summary = await self.memory_manager.extract_call_summary(old_turns, self.provider)
                except Exception as e:
                    logger.warning(f"Failed to compress call summary: {e}")

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

        filler = random.choice(VOICE_FILLER_PHRASES)
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
        
        summary = ""
        disposition = "resolved"

        try:
            summary = await self.memory_manager.extract_call_summary(self._call_turns, self.provider)
        except Exception as e:
            logger.warning(f"Failed to extract final summary: {e}")

        try:
            disposition = await self.memory_manager.get_call_disposition(self._call_turns, self.provider)
        except Exception as e:
            logger.warning(f"Failed to extract call disposition: {e}")

        try:
            await self.tool_executor.execute('log_disposition', {
                'call_id': self.session.call_id,
                'outcome': disposition,
                'disposition_note': summary
            })
        except Exception as e:
            logger.error(f"Failed to log disposition: {e}")

        return {
            'summary': summary,
            'disposition': disposition,
            'turn_count': self.session.turn_count
        }
