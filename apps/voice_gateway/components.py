"""
Builds the per-call AI pipeline (Hammad's voice layer) for one live call.

The gateway only wires components together; it does not change prompts,
retrieval behaviour or objection logic. Two thin adapters bridge interface
mismatches between VoiceOrchestrator and the shared chatbot components — see
their docstrings. Both should move into ai/ once Hammad has reviewed them.
"""

import logging
import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from apps.voice_gateway.call_store import CallStore

logger = logging.getLogger(__name__)

AudioSink = Callable[[bytes], Awaitable[None]]

DEFAULT_CALL_MODEL = "gpt-5.6-luna"  # what VoiceOrchestrator requests today
DEFAULT_RECORDING_DISCLOSURE = "This call may be recorded for quality purposes."


class StreamingProviderAdapter:
    """
    VoiceOrchestrator calls `provider.generate(..., stream=True)` and expects an
    async token iterator back. OpenAIProvider.generate() has no streaming path —
    it forwards stream=True to the SDK and then reads `response.choices`, which
    raises, so every turn fell through to the "having trouble" fallback line.
    Streaming lives in OpenAIProvider.stream(); route to it.
    """

    supports_streaming = True

    def __init__(self, provider: Any):
        self._provider = provider

    async def generate(self, messages: List[Dict[str, Any]], stream: bool = False, **kwargs):
        if stream:
            return self._provider.stream(messages, **kwargs)
        return await self._provider.generate(messages, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)


from ai.rag.retrieval import VoiceRetriever


@dataclass
class CallComponents:
    stt: Any
    tts: Any
    turn_manager: Any
    orchestrator: Any
    recording_enabled: bool
    recording_disclosure: str
    model_used: str


ComponentsFactory = Callable[[Dict[str, Any], CallStore, AudioSink], Awaitable[CallComponents]]

_base_provider = None


def _provider():
    # One provider (and its HTTP connection pool) per process, not per call.
    global _base_provider
    if _base_provider is None:
        from ai.providers.openai import OpenAIProvider
        _base_provider = OpenAIProvider()
    return _base_provider


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


async def build_call_components(call: Dict[str, Any], store: CallStore, audio_sink: AudioSink) -> CallComponents:
    # Imported here so the gateway app (and its tests) load without the
    # Deepgram/ElevenLabs SDKs until a real call arrives.
    from ai.memory.memory_manager import MemoryManager
    from ai.prompts.config import TenantConfigResolver
    from ai.rag.retrieval import SecurityEnforcedRetrieval
    from ai.stt_tts.deepgram import DeepgramSTT
    from ai.stt_tts.elevenlabs import ElevenLabsTTS
    from ai.stt_tts.turn_manager import TurnManager, VoiceSession
    from ai.tools.executor import ToolExecutor
    from ai.voice_orchestrator import VoiceOrchestrator
    from apps.api.supabase_client import get_supabase_admin

    supabase = get_supabase_admin()
    call_id, tenant_id, bot_id = str(call["id"]), str(call["tenant_id"]), str(call["bot_id"])

    resolver = TenantConfigResolver(supabase_client=supabase)
    bot = await resolver.get_bot_config(bot_id)
    if str(bot.get("tenant_id")) != tenant_id:
        raise PermissionError(f"Bot {bot_id} does not belong to tenant {tenant_id}")
    personality = resolver._apply_config_overrides(
        bot.get("system_personality") or "A helpful AI assistant.", bot
    )

    ivr = await store.load_ivr_config(tenant_id, bot_id)
    profile = ivr.get("voice_profiles") or {}

    tts = ElevenLabsTTS()
    voice_id = profile.get("voice_id") or ivr.get("voice_id")
    if voice_id:
        tts.default_voice_id = voice_id
    model_id = profile.get("model_id") or ivr.get("voice_model_id")
    if model_id:
        tts.model_id = model_id

    stt = DeepgramSTT()
    if ivr.get("stt_model"):
        stt.model = ivr["stt_model"]

    session = VoiceSession(call_id=call_id, tenant_id=tenant_id, bot_id=bot_id)
    turn_manager = TurnManager(
        session=session,
        endpointing_ms=float(ivr.get("stt_endpointing_ms") or _env_float("VOICE_ENDPOINTING_MS", 400)),
        dead_air_timeout_ms=_env_float("VOICE_DEAD_AIR_TIMEOUT_MS", 2000),
    )

    direction = call.get("direction", "inbound")
    tenant_config = {
        "business_name": bot.get("name", "our company"),
        "business_context": personality,
        "support_mode": bot.get("support_mode_enabled", True),
        "sales_mode": bot.get("sales_mode_enabled", True),
        "call_direction": direction,
    }
    if direction == "outbound":
        # The dial layer already enforced consent; passing caller_phone makes
        # the orchestrator re-check it at call start as a second gate.
        tenant_config["caller_phone"] = call.get("to_number", "")
        tenant_config["call_reason"] = await store.get_call_reason(call_id, tenant_id)

    provider = StreamingProviderAdapter(_provider())
    orchestrator = VoiceOrchestrator(
        provider=provider,
        retriever=VoiceRetriever(provider, SecurityEnforcedRetrieval(supabase_client=supabase)),
        tool_executor=ToolExecutor(supabase, tenant_id, bot_id, call_id=call_id),
        memory_manager=MemoryManager(recent_limit=10),
        turn_manager=turn_manager,
        tts=tts,
        session=session,
        tenant_config=tenant_config,
        latency_cache=None,
        audio_sink=audio_sink,
    )

    recording_enabled = (
        os.getenv("VOICE_RECORDING_ENABLED", "true").lower() != "false"
        and ivr.get("call_recording_enabled", True) is not False
    )
    return CallComponents(
        stt=stt,
        tts=tts,
        turn_manager=turn_manager,
        orchestrator=orchestrator,
        recording_enabled=recording_enabled,
        # Never record without a notice; a tenant may override the wording.
        recording_disclosure=(
            ivr.get("recording_disclosure_text") or DEFAULT_RECORDING_DISCLOSURE
        ) if recording_enabled else "",
        model_used=DEFAULT_CALL_MODEL,
    )
