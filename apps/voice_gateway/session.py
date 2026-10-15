"""
One live call: bridges a Twilio Media Stream WebSocket to the voice pipeline.

    Twilio ──µ-law──► gateway ──µ-law──► DeepgramSTT ──text──► TurnManager ──► VoiceOrchestrator
                         └──PCM16──► TurnManager.on_audio_chunk (barge-in energy)
    Twilio ◄──µ-law── gateway ◄──PCM16── ElevenLabsTTS ◄────────────────────────── VoiceOrchestrator

Twilio Media Streams protocol: https://www.twilio.com/docs/voice/media-streams/websocket-messages
"""

import asyncio
import base64
import json
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import WebSocket, WebSocketDisconnect

from apps.voice_gateway.audio import PCMToUlawStream, ulaw_to_pcm16
from apps.voice_gateway.call_store import CallStore
from apps.voice_gateway.components import CallComponents, ComponentsFactory
from apps.voice_gateway.opt_out import is_opt_out
from apps.voice_gateway import twilio_utils

logger = logging.getLogger(__name__)

ULAW_BYTES_PER_SECOND = 8000
END_CALL_TIMEOUT_S = 30


class CallSession:
    def __init__(self, ws: WebSocket, store: CallStore, factory: ComponentsFactory):
        self.ws = ws
        self.store = store
        self.factory = factory

        self.call: Optional[Dict[str, Any]] = None
        self.stream_sid: Optional[str] = None
        self.call_sid: Optional[str] = None
        self.c: Optional[CallComponents] = None

        self._send_lock = asyncio.Lock()
        self._encoder = PCMToUlawStream()
        self._audio_in: "asyncio.Queue[Optional[bytes]]" = asyncio.Queue()
        self._tasks: List[asyncio.Task] = []
        self._started_at = time.monotonic()
        self._inbound_bytes = 0

        # Twilio buffers whatever we send and plays it in real time, so TTS
        # usually finishes generating well before the caller has heard it.
        # Track when the buffered audio will run out so a caller who talks over
        # it can still interrupt.
        self._playback_until = 0.0

        # Latency: caller's last final transcript → first agent audio byte out.
        self._last_final_at: Optional[float] = None
        self._turn_started_at: Optional[float] = None
        self._pending_latency: Optional[int] = None
        self._latency_by_turn: Dict[int, int] = {}
        self._disclosure_text = ""
        self._opted_out = False
        # Awaited (never cancelled) on hang-up: "remove me" then an instant
        # hang-up must still land on the DNC list.
        self._compliance_tasks: List[asyncio.Task] = []

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        try:
            while True:
                msg = json.loads(await self.ws.receive_text())
                event = msg.get("event")
                if event == "start":
                    if not await self._on_start(msg):
                        await self.ws.close(code=1008)
                        return
                elif event == "media":
                    await self._on_media(msg)
                elif event == "stop":
                    logger.info(f"Stream stopped for call {self._call_id}")
                    break
                # "connected", "mark" and "dtmf" need no action in Phase 1.
        except WebSocketDisconnect:
            logger.info(f"Twilio WebSocket disconnected for call {self._call_id}")
        except Exception as e:
            logger.exception(f"Voice session error on call {self._call_id}: {e}")
        finally:
            await self._shutdown()

    @property
    def _call_id(self) -> str:
        return str(self.call["id"]) if self.call else "unknown"

    # ── Events ────────────────────────────────────────────────────────────────

    async def _on_start(self, msg: Dict[str, Any]) -> bool:
        start = msg.get("start", {})
        params = start.get("customParameters", {}) or {}
        call_id = params.get("call_id", "")

        if not twilio_utils.is_valid_stream_token(call_id, params.get("token")):
            logger.warning(f"Rejected media stream with invalid token (call_id={call_id!r})")
            return False

        # Tenant and bot come from our own call row, never from stream params.
        self.call = await self.store.get_call(call_id)
        if not self.call:
            logger.warning(f"Media stream for unknown call {call_id}")
            return False

        self.stream_sid = start.get("streamSid") or msg.get("streamSid")
        self.call_sid = start.get("callSid")
        await self.store.mark_stream_started(call_id, str(self.call["tenant_id"]), self.stream_sid)

        self.c = await self.factory(self.call, self.store, self._send_audio)
        self._hook_orchestrator()

        self._tasks.append(asyncio.create_task(self._stt_loop()))
        self._tasks.append(asyncio.create_task(self._open_call()))

        if self.c.recording_enabled and self.call_sid:
            callback = f"{twilio_utils.public_base_url()}/voice/recording-status"
            self._tasks.append(asyncio.create_task(
                twilio_utils.start_call_recording(self.call_sid, callback)
            ))

        logger.info(f"Call {call_id} live (stream {self.stream_sid}, tenant {self.call['tenant_id']})")
        return True

    async def _on_media(self, msg: Dict[str, Any]) -> None:
        if not self.c:
            return
        media = msg.get("media", {})
        if media.get("track", "inbound") != "inbound":
            return
        ulaw = base64.b64decode(media.get("payload", ""))
        self._inbound_bytes += len(ulaw)
        self._audio_in.put_nowait(ulaw)
        await self.c.turn_manager.on_audio_chunk(ulaw_to_pcm16(ulaw))

    # ── Pipeline tasks ────────────────────────────────────────────────────────

    async def _open_call(self) -> None:
        """Recording notice (if recording), then the orchestrator's greeting."""
        try:
            if self.c.recording_disclosure:
                self._disclosure_text = self.c.recording_disclosure
                async for chunk in self.c.tts.stream_text(self._disclosure_text):
                    await self._send_audio(chunk)
            await self.c.orchestrator.start()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"Failed to open call {self._call_id}: {e}")

    async def _caller_audio(self):
        while True:
            chunk = await self._audio_in.get()
            if chunk is None:
                return
            yield chunk

    async def _stt_loop(self) -> None:
        try:
            async for text, is_final in self.c.stt.stream(self._caller_audio()):
                if not text.strip():
                    continue
                if is_final:
                    self._last_final_at = time.monotonic()
                    if not self._opted_out and is_opt_out(text):
                        self._opted_out = True
                        self._compliance_tasks.append(asyncio.create_task(self._record_opt_out(text)))
                # The orchestrator has finished generating but Twilio is still
                # playing buffered audio — the caller is talking over it.
                if self._is_playing() and not getattr(self.c.orchestrator, "_agent_speaking", False):
                    await self._clear_playback()
                await self.c.turn_manager.on_stt_result(text, is_final)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"STT loop failed on call {self._call_id}: {e}")

    async def _record_opt_out(self, transcript: str) -> None:
        call = self.call
        number = call["to_number"] if call.get("direction") == "outbound" else call.get("from_number")
        try:
            await self.store.opt_out(str(call["tenant_id"]), number, self._call_id, f"Caller said: {transcript}")
            logger.info(f"Call {self._call_id}: caller opted out — {number} added to DNC")
        except Exception as e:
            # Loud: a missed opt-out is a compliance failure, not a glitch.
            logger.error(f"COMPLIANCE: failed to record opt-out for {number} on call {self._call_id}: {e}")

    def _hook_orchestrator(self) -> None:
        """
        Wrap two orchestrator handlers before start() registers them with the
        TurnManager (start() assigns self._handle_turn / self._handle_barge_in,
        which resolve to these instance attributes):
          * turn → measure caller-perceived latency per agent turn
          * barge-in → also flush the audio Twilio has already buffered
        """
        orch = self.c.orchestrator
        handle_turn, handle_barge_in = orch._handle_turn, orch._handle_barge_in

        async def timed_turn(transcript: str) -> None:
            self._turn_started_at = self._last_final_at or time.monotonic()
            try:
                await handle_turn(transcript)
            finally:
                self._turn_started_at = None
                lat = self._pending_latency
                turns = getattr(orch, "_call_turns", [])
                if lat is not None and turns and turns[-1]["speaker"] == "agent":
                    self._latency_by_turn[len(turns) - 1] = lat
                self._pending_latency = None

        async def barge_in() -> None:
            await handle_barge_in()
            await self._clear_playback()

        orch._handle_turn = timed_turn
        orch._handle_barge_in = barge_in

    # ── Audio out ─────────────────────────────────────────────────────────────

    def _is_playing(self) -> bool:
        return time.monotonic() < self._playback_until

    async def _send_audio(self, pcm: bytes) -> None:
        """audio_sink handed to the orchestrator: PCM16 8kHz in, Twilio media out."""
        ulaw = self._encoder.encode(pcm)
        if not ulaw or not self.stream_sid:
            return

        now = time.monotonic()
        if self._turn_started_at is not None and self._pending_latency is None:
            self._pending_latency = int((now - self._turn_started_at) * 1000)
            logger.info(f"Call {self._call_id} turn latency: {self._pending_latency} ms")

        await self._send({
            "event": "media",
            "streamSid": self.stream_sid,
            "media": {"payload": base64.b64encode(ulaw).decode()},
        })
        self._playback_until = max(self._playback_until, now) + len(ulaw) / ULAW_BYTES_PER_SECOND

    async def _clear_playback(self) -> None:
        if not self.stream_sid:
            return
        self._playback_until = 0.0
        self._encoder.reset()
        await self._send({"event": "clear", "streamSid": self.stream_sid})

    async def _send(self, payload: Dict[str, Any]) -> None:
        async with self._send_lock:
            try:
                await self.ws.send_text(json.dumps(payload))
            except Exception:
                pass  # socket already closed — the call is ending

    # ── Teardown ──────────────────────────────────────────────────────────────

    async def _shutdown(self) -> None:
        self._audio_in.put_nowait(None)
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await asyncio.gather(*self._compliance_tasks, return_exceptions=True)

        if not (self.call and self.c):
            return

        summary: Dict[str, Any] = {}
        try:
            summary = await asyncio.wait_for(self.c.orchestrator.end_call(), END_CALL_TIMEOUT_S)
        except Exception as e:
            logger.error(f"end_call failed for {self._call_id}: {e}")

        turns = [
            {**t, "latency_ms": self._latency_by_turn.get(i)}
            for i, t in enumerate(getattr(self.c.orchestrator, "_call_turns", []))
        ]
        if self._disclosure_text:
            turns.insert(0, {"speaker": "agent", "text": self._disclosure_text, "latency_ms": None})

        latencies = sorted(v for v in self._latency_by_turn.values())
        if latencies:
            p90 = latencies[min(len(latencies) - 1, int(round(0.9 * (len(latencies) - 1))))]
            logger.info(f"Call {self._call_id} latency ms — turns={latencies} p90={p90}")

        await self.store.finalize_call(
            call_id=self._call_id,
            tenant_id=str(self.call["tenant_id"]),
            direction=self.call.get("direction", "inbound"),
            turns=turns,
            summary=summary or {},
            stt_seconds=self._inbound_bytes / ULAW_BYTES_PER_SECOND,
            duration_s=int(time.monotonic() - self._started_at),
            model_used=self.c.model_used,
        )
