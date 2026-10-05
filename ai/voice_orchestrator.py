import asyncio
import logging
import random
from typing import Dict, Any, List, Optional

from ai.stt_tts.turn_manager import TurnManager, VoiceSession
from ai.stt_tts.elevenlabs import ElevenLabsTTS
from ai.memory.memory_manager import MemoryManager
from ai.tools.executor import ToolExecutor
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
    memory context, LLM streaming, objection handling, TTS streaming, barge-in,
    filler injection, and call logging for Phase 2.
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
        self.audio_sink = audio_sink  # Kashif's gateway injects this at call time

        # Internal state
        self._messages: List[Dict[str, str]] = []
        self._call_turns: List[Dict[str, str]] = []
        self._call_summary: str = ""
        self._objection_attempts: Dict[str, int] = {}
        self._agent_speaking: bool = False
        self._structured_memory: Dict[str, Any] = {}

        self._turn_lock = asyncio.Lock()

    async def start(self) -> None:
        """
        Called at call start to initialize the call, run consent checks,
        pre-fetch data, and play the initial greeting.
        """
        logger.info(f"Starting VoiceOrchestrator for call {self.session.call_id}")

        # If latency_cache provided: pre-fetch tenant config + pre-warm top-5 FAQ embeddings concurrently
        if self.latency_cache:
            try:
                await asyncio.gather(
                    self.latency_cache.prefetch_tenant_config(self.session.tenant_id),
                    self.latency_cache.prewarm_faq_embeddings(self.session.tenant_id, self.session.bot_id)
                )
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
                        return
                except Exception as e:
                    logger.warning(f"Consent check failed, blocking outbound call for safety: {e}")
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

        try:
            self._agent_speaking = True
            await self.turn_manager.on_agent_start_speaking()
            async for chunk in self.tts.stream_text(greeting):
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
        compiles prompt, streams LLM, and streams TTS.
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
                        res = await self.retriever.retrieve_with_fallback(tenant_id, transcript, [], limit=4)
                        return res.get('chunks', [])
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
            messages_to_send = [{"role": "system", "content": system_prompt_text}]
            messages_to_send.extend(self._messages[-6:])

            # 6. Stream LLM response
            full_response = ""
            try:
                self._agent_speaking = True
                await self.turn_manager.on_agent_start_speaking()

                # Determine if provider supports stream
                is_streaming = getattr(self.provider, 'supports_streaming', True) # Assumed True

                async def token_generator():
                    nonlocal full_response
                    try:
                        # Assuming provider.generate with stream=True returns an async generator of tokens
                        if is_streaming:
                            response_gen = await self.provider.generate(
                                messages=messages_to_send,
                                model="gpt-5.6-luna",
                                temperature=0.3,
                                stream=True
                            )
                            # Handle both cases where it might return an async generator directly or a dict
                            if hasattr(response_gen, '__aiter__'):
                                async for token in response_gen:
                                    full_response += token
                                    yield token
                            else:
                                text = response_gen.get("text", "")
                                full_response = text
                                yield text
                        else:
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

                # 7. Feed LLM output through tts.stream_sentences() → audio_sink → Twilio gateway
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

    async def _handle_filler(self) -> None:
        """
        Picks a random filler phrase and streams it via TTS immediately.
        """
        filler = random.choice(VOICE_FILLER_PHRASES)
        logger.info(f"Injecting filler phrase: '{filler}'")
        try:
            self._agent_speaking = True
            await self.turn_manager.on_agent_start_speaking()
            async for chunk in self.tts.stream_text(filler):
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
