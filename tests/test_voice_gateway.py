# ============================================================================
# Voice Gateway tests — Twilio Media Streams ⇄ voice pipeline (Kashif)
#
# Covers:
#   - µ-law <-> PCM16 codec used at the Twilio boundary
#   - Twilio webhook signature + media-stream token verification
#   - /voice/inbound: number → tenant routing, TwiML, signature rejection
#   - /voice/stream: a full simulated call through the REAL VoiceOrchestrator
#     and TurnManager, with Deepgram / ElevenLabs / OpenAI / Supabase stubbed
#   - barge-in flushes Twilio's buffered audio ("clear" event)
#
# Run: pytest tests/test_voice_gateway.py -v
# ============================================================================

import asyncio
import base64
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ["TWILIO_AUTH_TOKEN"] = "test-auth-token"
os.environ["VOICE_PUBLIC_BASE_URL"] = "https://voice.example.com"

from apps.voice_gateway import twilio_utils
from apps.voice_gateway.audio import PCMToUlawStream, pcm16_to_ulaw, ulaw_to_pcm16
from apps.voice_gateway.components import CallComponents, StreamingProviderAdapter, VoiceRetriever
from apps.voice_gateway.main import app

TENANT = "11111111-1111-1111-1111-111111111111"
BOT = "22222222-2222-2222-2222-222222222222"
CALL = "33333333-3333-3333-3333-333333333333"


# ─────────────────────────────────────────────────────────────────────────────
# Audio codec
# ─────────────────────────────────────────────────────────────────────────────

class TestAudioCodec:
    def test_known_ulaw_values(self):
        pcm = np.frombuffer(ulaw_to_pcm16(bytes([0xFF, 0x7F, 0x00, 0x80])), dtype="<i2")
        assert list(pcm) == [0, 0, -32124, 32124]

    def test_silence_encodes_to_0xff(self):
        assert pcm16_to_ulaw(b"\x00\x00" * 4) == b"\xff" * 4

    def test_roundtrip_is_close(self):
        t = np.arange(800) / 8000
        sine = (np.sin(2 * np.pi * 440 * t) * 12000).astype("<i2")
        back = np.frombuffer(ulaw_to_pcm16(pcm16_to_ulaw(sine.tobytes())), dtype="<i2")
        # µ-law is lossy: error is bounded relative to amplitude (~3%).
        assert np.max(np.abs(back.astype(int) - sine.astype(int))) < 12000 * 0.04

    def test_every_ulaw_byte_roundtrips(self):
        all_bytes = bytes(range(256))
        again = pcm16_to_ulaw(ulaw_to_pcm16(all_bytes))
        # 0x7F and 0xFF both mean zero; everything else must be exact.
        assert all(a == b or {a, b} == {0x7F, 0xFF} for a, b in zip(all_bytes, again))

    def test_stream_encoder_handles_odd_chunk_boundaries(self):
        pcm = (np.arange(-500, 500, dtype="<i2") * 30).tobytes()
        enc = PCMToUlawStream()
        split = enc.encode(pcm[:333]) + enc.encode(pcm[333:777]) + enc.encode(pcm[777:])
        assert split == pcm16_to_ulaw(pcm)


# ─────────────────────────────────────────────────────────────────────────────
# Twilio verification + TwiML
# ─────────────────────────────────────────────────────────────────────────────

class TestTwilioUtils:
    def test_signature_matches_twilio_reference_example(self):
        # Example from Twilio's "Webhooks security" documentation.
        params = {
            "CallSid": "CA1234567890ABCDE", "Caller": "+12349013030", "Digits": "1234",
            "From": "+12349013030", "To": "+18005551212",
        }
        sig = twilio_utils.compute_signature(
            "12345", "https://mycompany.com/myapp.php?foo=1&bar=2", params
        )
        assert sig == "0/KCTR6DLpKmkAf8muzZqo1nDgQ="

    def test_signature_rejects_tampered_params(self):
        url, params = "https://voice.example.com/voice/inbound", {"To": "+15550001111"}
        sig = twilio_utils.compute_signature("tok", url, params)
        assert twilio_utils.is_valid_signature("tok", url, params, sig)
        assert not twilio_utils.is_valid_signature("tok", url, {"To": "+15559999999"}, sig)
        assert not twilio_utils.is_valid_signature("tok", url, params, None)

    def test_stream_token(self):
        tok = twilio_utils.stream_token(CALL)
        assert twilio_utils.is_valid_stream_token(CALL, tok)
        assert not twilio_utils.is_valid_stream_token(CALL, tok[:-1] + "0")
        assert not twilio_utils.is_valid_stream_token("other-call", tok)
        assert not twilio_utils.is_valid_stream_token(CALL, None)

    def test_twiml_escapes_attributes(self):
        xml = twilio_utils.twiml_connect_stream("wss://x/voice/stream", {"call_id": 'a"<b'})
        assert "<Connect><Stream url=" in xml
        assert 'a"<b' not in xml


# ─────────────────────────────────────────────────────────────────────────────
# Fakes
# ─────────────────────────────────────────────────────────────────────────────

class FakeStore:
    def __init__(self):
        self.numbers = {"+15550001111": {"id": "pn-1", "tenant_id": TENANT, "bot_id": BOT}}
        self.calls = {}
        self.finalized = None

    async def find_inbound_number(self, to_number):
        return self.numbers.get(to_number)

    async def create_call(self, **kw):
        row = {"id": CALL, **kw}
        self.calls[CALL] = row
        return row

    async def get_call(self, call_id):
        return self.calls.get(call_id)

    async def mark_stream_started(self, call_id, tenant_id, stream_sid):
        self.calls[call_id]["twilio_stream_sid"] = stream_sid

    async def load_ivr_config(self, tenant_id, bot_id):
        return {}

    async def update_status_by_sid(self, *a):
        pass

    async def save_recording(self, **kw):
        pass

    async def finalize_call(self, **kw):
        self.finalized = kw


PCM_CHUNK = (np.full(160, 4000, dtype="<i2")).tobytes()  # 20ms of non-silent audio


class FakeTTS:
    """Mimics ElevenLabsTTS: PCM16 chunks out, cancel() stops the stream."""

    def __init__(self, greeting_delay=0.0):
        self.spoken = []
        self.greeting_delay = greeting_delay
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    async def stream_text(self, text, voice_id=None):
        self._cancelled = False
        self.spoken.append(text)
        yield PCM_CHUNK
        if self.greeting_delay:
            await asyncio.sleep(self.greeting_delay)
            if not self._cancelled:
                yield PCM_CHUNK

    async def stream_sentences(self, token_gen, voice_id=None):
        self._cancelled = False
        text = "".join([t async for t in token_gen])
        self.spoken.append(text)
        yield PCM_CHUNK


class FakeSTT:
    """Emits one final transcript shortly after caller audio starts."""

    def __init__(self, transcript):
        self.transcript = transcript

    async def stream(self, audio_gen):
        sent = False
        async for _ in audio_gen:
            if not sent:
                sent = True
                await asyncio.sleep(0.15)  # let the greeting's on_agent_done_speaking land
                yield self.transcript, True


class FakeBaseProvider:
    def __init__(self):
        self.prompts = []

    async def stream(self, messages, **kw):
        self.prompts.append(messages[0]["content"])
        for tok in ["We're open ", "nine to six, ", "Monday to Friday."]:
            yield tok

    async def generate(self, messages, **kw):
        return {"text": "resolved"}

    async def embed(self, text, **kw):
        return [0.1, 0.2]


def make_factory(provider, tts, stt_text, retrieved):
    from ai.memory.memory_manager import MemoryManager
    from ai.stt_tts.turn_manager import TurnManager, VoiceSession
    from ai.voice_orchestrator import VoiceOrchestrator

    async def factory(call, store, audio_sink):
        session = VoiceSession(call_id=call["id"], tenant_id=call["tenant_id"], bot_id=call["bot_id"])
        tm = TurnManager(session=session, endpointing_ms=50, dead_air_timeout_ms=60000)
        rag = MagicMock()
        rag.retrieve_with_fallback = AsyncMock(return_value={"chunks": [{"content": retrieved}]})
        tools = MagicMock()
        tools.execute = AsyncMock(return_value={"status": "disposition_logged"})
        adapter = StreamingProviderAdapter(provider)
        orch = VoiceOrchestrator(
            provider=adapter,
            retriever=VoiceRetriever(adapter, rag),
            tool_executor=tools,
            memory_manager=MemoryManager(),
            turn_manager=tm,
            tts=tts,
            session=session,
            tenant_config={"business_name": "OrbitWorks", "call_direction": "inbound"},
            audio_sink=audio_sink,
        )
        return CallComponents(
            stt=FakeSTT(stt_text), tts=tts, turn_manager=tm, orchestrator=orch,
            recording_enabled=False, recording_disclosure="", model_used="gpt-5.6-luna",
        )

    return factory


@pytest.fixture
def store():
    s = FakeStore()
    app.state.store = s
    yield s
    app.state.store = None


def _start_msg(call_id=CALL, token=None):
    return json.dumps({
        "event": "start",
        "streamSid": "MZ123",
        "start": {
            "streamSid": "MZ123", "callSid": "CA123",
            "customParameters": {"call_id": call_id, "token": token or twilio_utils.stream_token(call_id)},
        },
    })


def _media_msg(ulaw: bytes):
    return json.dumps({
        "event": "media", "streamSid": "MZ123",
        "media": {"track": "inbound", "payload": base64.b64encode(ulaw).decode()},
    })


SILENCE = b"\xff" * 160
LOUD = pcm16_to_ulaw((np.full(160, 20000, dtype="<i2")).tobytes())


# ─────────────────────────────────────────────────────────────────────────────
# Webhooks
# ─────────────────────────────────────────────────────────────────────────────

class TestInboundWebhook:
    def _post(self, client, form, sign=True):
        headers = {}
        if sign:
            headers["X-Twilio-Signature"] = twilio_utils.compute_signature(
                "test-auth-token", "https://voice.example.com/voice/inbound", form
            )
        return client.post("/voice/inbound", data=form, headers=headers)

    def test_known_number_connects_stream(self, store):
        client = TestClient(app)
        r = self._post(client, {"CallSid": "CA1", "From": "+15557770000", "To": "+15550001111"})
        assert r.status_code == 200
        assert "<Connect><Stream url=\"wss://voice.example.com/voice/stream\">" in r.text
        assert f'value="{CALL}"' in r.text
        assert twilio_utils.stream_token(CALL) in r.text
        assert store.calls[CALL]["tenant_id"] == TENANT
        assert store.calls[CALL]["direction"] == "inbound"

    def test_unknown_number_hangs_up(self, store):
        r = self._post(TestClient(app), {"CallSid": "CA2", "From": "+1", "To": "+19990000000"})
        assert "<Hangup/>" in r.text and "<Connect>" not in r.text
        assert store.calls == {}

    def test_unsigned_request_rejected(self, store):
        r = self._post(TestClient(app), {"CallSid": "CA3", "To": "+15550001111"}, sign=False)
        assert r.status_code == 403
        assert store.calls == {}


# ─────────────────────────────────────────────────────────────────────────────
# Media stream — end-to-end simulated call
# ─────────────────────────────────────────────────────────────────────────────

class TestMediaStream:
    def test_invalid_token_is_rejected(self, store):
        store.calls[CALL] = {"id": CALL, "tenant_id": TENANT, "bot_id": BOT, "direction": "inbound"}
        app.state.components_factory = make_factory(FakeBaseProvider(), FakeTTS(), "hi", "x")
        with TestClient(app).websocket_connect("/voice/stream") as ws:
            ws.send_text(_start_msg(token="forged"))
            with pytest.raises(Exception):
                ws.receive_text()
        assert store.finalized is None

    def test_full_call_greeting_grounded_answer_and_persistence(self, store):
        store.calls[CALL] = {"id": CALL, "tenant_id": TENANT, "bot_id": BOT, "direction": "inbound"}
        provider, tts = FakeBaseProvider(), FakeTTS()
        app.state.components_factory = make_factory(
            provider, tts, "What are your business hours?", "Hours: Mon-Fri 9AM-6PM PKT"
        )

        with TestClient(app).websocket_connect("/voice/stream") as ws:
            ws.send_text(json.dumps({"event": "connected"}))
            ws.send_text(_start_msg())

            greeting = json.loads(ws.receive_text())
            assert greeting["event"] == "media" and greeting["streamSid"] == "MZ123"
            # Outbound audio must be µ-law: one byte per PCM16 sample.
            assert len(base64.b64decode(greeting["media"]["payload"])) == len(PCM_CHUNK) // 2

            ws.send_text(_media_msg(SILENCE))
            answer = json.loads(ws.receive_text())
            assert answer["event"] == "media"

            ws.send_text(json.dumps({"event": "stop", "streamSid": "MZ123"}))

        # Grounding: retrieved chunk text reached the compiled voice prompt.
        assert any("Mon-Fri 9AM-6PM PKT" in p for p in provider.prompts)
        # Streaming adapter: real tokens were spoken, not the error fallback.
        assert tts.spoken[-1] == "We're open nine to six, Monday to Friday."

        f = store.finalized
        assert f is not None and f["tenant_id"] == TENANT and f["call_id"] == CALL
        speakers = [(t["speaker"], t["text"]) for t in f["turns"]]
        assert speakers[1] == ("caller", "What are your business hours?")
        assert speakers[2] == ("agent", "We're open nine to six, Monday to Friday.")
        assert f["turns"][2]["latency_ms"] is not None
        assert f["stt_seconds"] > 0

    def test_barge_in_clears_twilio_buffer(self, store):
        store.calls[CALL] = {"id": CALL, "tenant_id": TENANT, "bot_id": BOT, "direction": "inbound"}
        tts = FakeTTS(greeting_delay=1.0)
        app.state.components_factory = make_factory(FakeBaseProvider(), tts, "", "x")

        with TestClient(app).websocket_connect("/voice/stream") as ws:
            ws.send_text(_start_msg())
            assert json.loads(ws.receive_text())["event"] == "media"
            # Caller talks loudly while the greeting is still being spoken.
            ws.send_text(_media_msg(LOUD))
            assert json.loads(ws.receive_text())["event"] == "clear"
            ws.send_text(json.dumps({"event": "stop"}))
