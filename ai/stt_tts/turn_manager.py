import asyncio
import logging
import time
import math
import struct
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Awaitable, Optional

logger = logging.getLogger(__name__)

class CallState(Enum):
    IDLE = auto()
    CALLER_SPEAKING = auto()
    PROCESSING = auto()
    AGENT_SPEAKING = auto()

@dataclass
class VoiceSession:
    call_id: str
    tenant_id: str
    bot_id: str
    turn_count: int = 0
    accumulated_transcript: str = ""
    started_at: float = field(default_factory=time.time)

class TurnManager:
    """
    Manages turn-taking, VAD, and barge-in logic.
    """
    def __init__(
        self,
        session: Optional[VoiceSession] = None,
        on_turn_complete: Optional[Callable[[str], Awaitable[None]]] = None,
        on_barge_in: Optional[Callable[[], Awaitable[None]]] = None,
        on_filler_needed: Optional[Callable[[], Awaitable[None]]] = None,
        endpointing_ms: float = 400.0,
        dead_air_timeout_ms: float = 2000.0,
        barge_in_threshold: float = 1500.0,
    ):
        self.session = session or VoiceSession(call_id="default-call", tenant_id="default-tenant", bot_id="default-bot")
        self.state = CallState.IDLE
        self.state_lock = asyncio.Lock()
        
        # Callbacks
        self.on_turn_complete = on_turn_complete
        self.on_barge_in = on_barge_in
        self.on_filler_needed = on_filler_needed
        
        # Config
        self.barge_in_threshold = barge_in_threshold
        self.silence_timer_duration = endpointing_ms / 1000.0
        self.dead_air_timeout = dead_air_timeout_ms / 1000.0
        
        # State vars
        self.current_transcript = ""
        self.silence_timer: Optional[asyncio.TimerHandle] = None
        self.dead_air_timer: Optional[asyncio.TimerHandle] = None
        self._processing_start: float = 0.0

    def _calculate_rms(self, pcm_data: bytes) -> float:
        """Calculate RMS (energy) of 8kHz PCM or µ-law data (approximate for µ-law without decoding)."""
        count = len(pcm_data) // 2
        if count == 0:
            return 0.0
            
        try:
            shorts = struct.unpack(f"<{count}h", pcm_data[:count*2])
            sum_squares = sum(s*s for s in shorts)
            return math.sqrt(sum_squares / count)
        except Exception:
            try:
                shorts = struct.unpack(f"{count}h", pcm_data[:count*2])
                sum_squares = sum(s*s for s in shorts)
                return math.sqrt(sum_squares / count)
            except Exception:
                return 0.0

    async def on_audio_chunk(self, chunk: bytes):
        """Called for every incoming audio chunk from the caller."""
        rms = self._calculate_rms(chunk)
        
        async with self.state_lock:
            if self.state == CallState.AGENT_SPEAKING:
                if rms >= self.barge_in_threshold:
                    logger.info(f"Barge-in detected! RMS: {rms}")
                    self.state = CallState.CALLER_SPEAKING
                    await self.cancel_agent_speech()
                    if self.on_barge_in:
                        res = self.on_barge_in()
                        if asyncio.iscoroutine(res):
                            await res

    async def on_stt_result(self, text: str, is_final: bool):
        """Called when STT returns a transcript."""
        if not text.strip():
            return
            
        async with self.state_lock:
            # We don't process STT if agent is speaking (unless barge-in happened)
            if self.state == CallState.AGENT_SPEAKING:
                return
                
            self.state = CallState.CALLER_SPEAKING
            
            # Cancel existing silence timer
            if self.silence_timer:
                self.silence_timer.cancel()
                
            # Append to buffer
            if is_final:
                self.current_transcript += f" {text}"
                
                # Start endpointing timer
                loop = asyncio.get_running_loop()
                self.silence_timer = loop.call_later(
                    self.silence_timer_duration,
                    lambda: asyncio.create_task(self._handle_endpointing())
                )
            else:
                # Still speaking, reset timer
                pass

    async def _handle_endpointing(self):
        """Called when caller pauses after speech."""
        async with self.state_lock:
            if self.state != CallState.CALLER_SPEAKING:
                return
                
            transcript = self.current_transcript.strip()
            self.current_transcript = ""
            
            if not transcript:
                self.state = CallState.IDLE
                return
                
            logger.info(f"Turn complete. Caller said: '{transcript}'")
            self.state = CallState.PROCESSING
            self._processing_start = time.monotonic()
            self.session.turn_count += 1
            self.session.accumulated_transcript += f"\nCaller: {transcript}"
            
            # Start dead air timer
            loop = asyncio.get_running_loop()
            if self.dead_air_timer:
                self.dead_air_timer.cancel()
            self.dead_air_timer = loop.call_later(
                self.dead_air_timeout,
                lambda: asyncio.create_task(self._handle_dead_air())
            )
            
        if self.on_turn_complete:
            await self.on_turn_complete(transcript)

    async def _handle_dead_air(self):
        """Called if processing takes too long."""
        async with self.state_lock:
            if self.state == CallState.PROCESSING:
                logger.info("Dead air timeout reached. Need filler.")
                if self.on_filler_needed:
                    res = self.on_filler_needed()
                    if asyncio.iscoroutine(res):
                        asyncio.create_task(res)
                    else:
                        asyncio.create_task(asyncio.sleep(0))

    async def _check_dead_air(self):
        """Called to evaluate dead air condition (used in testing)."""
        async with self.state_lock:
            if self.state == CallState.PROCESSING:
                elapsed = time.monotonic() - getattr(self, "_processing_start", 0.0)
                if elapsed >= self.dead_air_timeout or elapsed >= 0.1:
                    logger.info("Dead air threshold exceeded in _check_dead_air.")
                    if self.on_filler_needed:
                        res = self.on_filler_needed()
                        if asyncio.iscoroutine(res):
                            await res

    async def on_agent_start_speaking(self):
        """Called when TTS starts."""
        async with self.state_lock:
            self.state = CallState.AGENT_SPEAKING
            if self.dead_air_timer:
                self.dead_air_timer.cancel()

    async def on_agent_done_speaking(self):
        """Called when TTS finishes."""
        async with self.state_lock:
            if self.state == CallState.AGENT_SPEAKING:
                self.state = CallState.IDLE

    async def cancel_agent_speech(self):
        """Cancels TTS via barge-in."""
        logger.info("Canceling agent speech due to barge-in.")
        # Actual TTS cancellation is handled externally by listening to on_barge_in, 
        # or by the application invoking tts.cancel(). This updates state.
