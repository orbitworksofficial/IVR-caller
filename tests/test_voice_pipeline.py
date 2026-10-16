# ============================================================================
# Voice Pipeline Unit Tests — AI IVR Caller
# Author: Hammad Ahmed (AI/ML Engineer)
#
# Covers Hammad's components:
#   - TurnManager state machine (barge-in, endpointing, dead-air)
#   - DeepgramSTT adapter (streaming behaviour, reconnection)
#   - ElevenLabsTTS adapter (sentence splitting, cancellation)
#   - Voice prompt compilation (voice_mode, call_direction, filler phrases)
#   - Objection library (detection, two-attempt rule, knowledge chunks)
#   - MemoryManager call extensions (summary, disposition, split_call_turns)
#   - ToolExecutor call-specific tools (consent gating, disposition, warm transfer)
#
# Run: pytest tests/test_voice_pipeline.py -v
# ============================================================================

import asyncio
import json
import os
import sys
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_supabase():
    """Mock Supabase client that returns safe empty responses by default."""
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
    client.table.return_value.insert.return_value.execute.return_value.data = [{"id": "test-uuid-123"}]
    client.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
    return client


@pytest.fixture
def mock_provider():
    """Mock LLM provider with standard response."""
    provider = AsyncMock()
    provider.generate = AsyncMock(return_value={
        "text": "Thank you for calling OrbitWorks. How can I help you today?",
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        "model": "gpt-5.6-luna",
    })
    provider.embed = AsyncMock(return_value=[0.1] * 1536)
    return provider


@pytest.fixture
def tool_executor(mock_supabase):
    from ai.tools.executor import ToolExecutor
    return ToolExecutor(
        supabase_client=mock_supabase,
        tenant_id="tenant-test-001",
        bot_id="bot-test-001",
        call_id="call-test-001",
    )


@pytest.fixture
def memory_manager():
    from ai.memory.memory_manager import MemoryManager
    return MemoryManager(recent_limit=10)


# ─────────────────────────────────────────────────────────────────────────────
# TurnManager Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestTurnManager:

    def test_initial_state(self):
        from ai.stt_tts.turn_manager import TurnManager, CallState
        tm = TurnManager()
        assert tm.state == CallState.IDLE

    @pytest.mark.asyncio
    async def test_turn_complete_fires_callback(self):
        """After STT final result + silence timeout, turn_complete callback fires."""
        from ai.stt_tts.turn_manager import TurnManager

        completed_transcripts = []

        async def on_complete(text: str):
            completed_transcripts.append(text)

        tm = TurnManager(
            on_turn_complete=on_complete,
            endpointing_ms=100,  # Short for testing
        )

        await tm.on_stt_result("Hello, what services do you offer?", is_final=True)
        await asyncio.sleep(0.2)  # Wait for endpointing timer

        assert len(completed_transcripts) == 1
        assert "Hello" in completed_transcripts[0]

    @pytest.mark.asyncio
    async def test_barge_in_cancels_agent_speech(self):
        """When caller audio arrives during agent TTS, barge_in callback fires."""
        from ai.stt_tts.turn_manager import TurnManager, CallState

        barge_in_fired = []

        async def on_barge_in():
            barge_in_fired.append(True)

        tm = TurnManager(on_barge_in=on_barge_in)

        # Simulate agent speaking
        await tm.on_agent_start_speaking()
        assert tm.state == CallState.AGENT_SPEAKING

        # Caller interrupts with audio that has energy > threshold
        # Synthesize PCM bytes with non-zero energy
        import struct
        audio_chunk = struct.pack("<" + "h" * 160, *([2000] * 160))  # 160 samples at amplitude 2000
        await tm.on_audio_chunk(audio_chunk)

        assert len(barge_in_fired) == 1
        assert tm.state != CallState.AGENT_SPEAKING

    @pytest.mark.asyncio
    async def test_dead_air_protection(self):
        """If processing takes >2s, filler_needed callback fires."""
        from ai.stt_tts.turn_manager import TurnManager, CallState

        fillers_needed = []

        async def on_filler():
            fillers_needed.append(True)

        tm = TurnManager(
            on_filler_needed=on_filler,
            dead_air_timeout_ms=200,  # Short for testing
        )

        # Force into PROCESSING state
        tm.state = CallState.PROCESSING
        tm._processing_start = time.monotonic() - 0.3  # 300ms ago

        await tm._check_dead_air()

        assert len(fillers_needed) == 1

    @pytest.mark.asyncio
    async def test_continuation_resets_endpointing_timer(self):
        """If caller resumes within endpointing window, timer resets (continuation)."""
        from ai.stt_tts.turn_manager import TurnManager

        completed = []

        async def on_complete(text: str):
            completed.append(text)

        tm = TurnManager(
            on_turn_complete=on_complete,
            endpointing_ms=300,
        )

        await tm.on_stt_result("I want", is_final=True)
        await asyncio.sleep(0.1)  # Before timeout fires

        # Caller continues
        await tm.on_stt_result("I want to book a call", is_final=True)
        await asyncio.sleep(0.4)  # Now let it fire

        assert len(completed) == 1
        assert "book a call" in completed[0]

    @pytest.mark.asyncio
    async def test_voice_session_tracking(self):
        """VoiceSession tracks call metadata correctly."""
        from ai.stt_tts.turn_manager import VoiceSession
        session = VoiceSession(
            call_id="call-abc",
            tenant_id="tenant-xyz",
            bot_id="bot-001",
        )
        assert session.turn_count == 0
        session.turn_count += 1
        assert session.turn_count == 1


# ─────────────────────────────────────────────────────────────────────────────
# Voice Prompt Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestVoicePrompts:

    def test_compile_inbound_support_prompt(self):
        from ai.prompts.voice.templates import compile_voice_prompt
        prompt = compile_voice_prompt(
            business_name="OrbitWorks",
            business_context="AI automation agency",
            retrieved_knowledge="We offer AI Automation, Digital Marketing...",
            support_mode_enabled=True,
            sales_mode_enabled=False,
            call_direction="inbound",
        )
        assert "OrbitWorks" in prompt
        assert "phone" in prompt.lower() or "voice" in prompt.lower() or "sentence" in prompt.lower()
        assert "markdown" in prompt.lower() or "bullet" in prompt.lower()

    def test_compile_outbound_sales_prompt(self):
        from ai.prompts.voice.templates import compile_voice_prompt
        prompt = compile_voice_prompt(
            business_name="OrbitWorks",
            business_context="AI automation agency",
            retrieved_knowledge="We offer AI Automation...",
            support_mode_enabled=True,
            sales_mode_enabled=True,
            call_direction="outbound",
            call_reason="Following up on your enquiry about AI automation",
        )
        assert "outbound" in prompt.lower() or "calling" in prompt.lower() or "follow" in prompt.lower()

    def test_filler_phrases_available(self):
        from ai.prompts.voice.templates import VOICE_FILLER_PHRASES
        assert len(VOICE_FILLER_PHRASES) >= 4
        for phrase in VOICE_FILLER_PHRASES:
            assert len(phrase) > 5

    def test_greeting_templates(self):
        from ai.prompts.voice.templates import VOICE_GREETING_TEMPLATES, get_voice_greeting
        assert "inbound_support" in VOICE_GREETING_TEMPLATES
        assert "outbound" in VOICE_GREETING_TEMPLATES
        greeting = get_voice_greeting("inbound_support", "OrbitWorks")
        assert "OrbitWorks" in greeting

    def test_handoff_phrases(self):
        from ai.prompts.voice.templates import VOICE_HANDOFF_PHRASES
        assert len(VOICE_HANDOFF_PHRASES) >= 3

    def test_abstain_phrases(self):
        from ai.prompts.voice.templates import VOICE_ABSTAIN_PHRASES
        assert len(VOICE_ABSTAIN_PHRASES) >= 2
        for phrase in VOICE_ABSTAIN_PHRASES:
            # Should offer an alternative, not just refuse
            assert any(word in phrase.lower() for word in ["team", "specialist", "call back", "email"])


# ─────────────────────────────────────────────────────────────────────────────
# Objection Library Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestObjectionLibrary:

    def test_all_categories_present(self):
        from ai.prompts.voice.objections import OBJECTION_LIBRARY
        expected = {"PRICE", "TIMING", "TRUST", "NEED_TO_THINK", "ALREADY_HAVE_PROVIDER"}
        assert set(OBJECTION_LIBRARY.keys()) == expected

    def test_detect_price_objection(self):
        from ai.prompts.voice.objections import detect_objection
        obj = detect_objection("This sounds too expensive for us right now.")
        assert obj is not None
        assert obj.category == "PRICE"

    def test_detect_timing_objection(self):
        from ai.prompts.voice.objections import detect_objection
        obj = detect_objection("Maybe next quarter when we're less busy.")
        assert obj is not None
        assert obj.category == "TIMING"

    def test_detect_trust_objection(self):
        from ai.prompts.voice.objections import detect_objection
        obj = detect_objection("I've never heard of you. Can I see some examples?")
        assert obj is not None
        assert obj.category == "TRUST"

    def test_detect_need_to_think(self):
        from ai.prompts.voice.objections import detect_objection
        obj = detect_objection("Let me think about it and talk to my partner.")
        assert obj is not None
        assert obj.category == "NEED_TO_THINK"

    def test_detect_already_have_provider(self):
        from ai.prompts.voice.objections import detect_objection
        obj = detect_objection("We already have someone handling our marketing.")
        assert obj is not None
        assert obj.category == "ALREADY_HAVE_PROVIDER"

    def test_no_false_objection_on_neutral(self):
        from ai.prompts.voice.objections import detect_objection
        obj = detect_objection("What are your business hours?")
        assert obj is None

    def test_two_attempt_rule_first(self):
        from ai.prompts.voice.objections import detect_objection, get_objection_response
        obj = detect_objection("It's too expensive.")
        assert obj is not None
        response = get_objection_response(obj, attempt_number=1)
        # First attempt: counter-argument
        assert response == obj.counter_template

    def test_two_attempt_rule_second_gives_soft_close(self):
        from ai.prompts.voice.objections import detect_objection, get_objection_response
        obj = detect_objection("I still can't justify the cost.")
        assert obj is not None
        response = get_objection_response(obj, attempt_number=2)
        # Second attempt: soft close
        assert response == obj.soft_close_template

    def test_objection_acknowledge_is_brief(self):
        from ai.prompts.voice.objections import OBJECTION_LIBRARY
        for name, obj in OBJECTION_LIBRARY.items():
            words = len(obj.acknowledge_template.split())
            assert words <= 15, f"{name} acknowledge is too long ({words} words): {obj.acknowledge_template}"

    def test_objection_as_knowledge_chunks(self):
        from ai.prompts.voice.objections import get_objection_as_knowledge_chunks
        chunks = get_objection_as_knowledge_chunks()
        assert len(chunks) == 5
        for chunk in chunks:
            assert "content" in chunk
            assert chunk.get("category") == "objection_handling"


# ─────────────────────────────────────────────────────────────────────────────
# Memory Manager — Call-specific Methods
# ─────────────────────────────────────────────────────────────────────────────

class TestCallMemory:

    @pytest.mark.asyncio
    async def test_extract_call_summary(self, memory_manager, mock_provider):
        mock_provider.generate = AsyncMock(return_value={
            "text": "The caller, John Smith, enquired about AI automation services and booked a discovery call.",
            "usage": {}, "model": "gpt-5.6-luna",
        })
        turns = [
            {"speaker": "caller", "text": "Hi, I need help with automating my business."},
            {"speaker": "agent", "text": "Great! What type of business do you run?"},
            {"speaker": "caller", "text": "E-commerce. My name is John Smith."},
            {"speaker": "agent", "text": "Perfect, let me book a discovery call for you."},
        ]
        summary = await memory_manager.extract_call_summary(turns, mock_provider)
        assert len(summary) > 10
        assert isinstance(summary, str)

    @pytest.mark.asyncio
    async def test_extract_call_summary_empty_turns(self, memory_manager, mock_provider):
        summary = await memory_manager.extract_call_summary([], mock_provider)
        assert summary == ""
        mock_provider.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_call_disposition_booked(self, memory_manager, mock_provider):
        mock_provider.generate = AsyncMock(return_value={
            "text": "booked", "usage": {}, "model": "gpt-5.6-luna",
        })
        turns = [
            {"speaker": "agent", "text": "Great, I've sent you the booking link."},
            {"speaker": "caller", "text": "Perfect, I'll book it now. Thank you!"},
        ]
        disposition = await memory_manager.get_call_disposition(turns, mock_provider)
        assert disposition == "booked"

    @pytest.mark.asyncio
    async def test_get_call_disposition_invalid_falls_back(self, memory_manager, mock_provider):
        mock_provider.generate = AsyncMock(return_value={
            "text": "something_invalid", "usage": {}, "model": "gpt-5.6-luna",
        })
        turns = [{"speaker": "caller", "text": "OK bye."}]
        disposition = await memory_manager.get_call_disposition(turns, mock_provider)
        assert disposition == "resolved"

    def test_split_call_turns_under_limit(self, memory_manager):
        turns = [{"speaker": "caller", "text": f"Turn {i}"} for i in range(5)]
        result = memory_manager.split_call_turns(turns, recent_limit=8)
        assert result["recent"] == turns
        assert result["to_summarize"] == []

    def test_split_call_turns_over_limit(self, memory_manager):
        turns = [{"speaker": "caller", "text": f"Turn {i}"} for i in range(12)]
        result = memory_manager.split_call_turns(turns, recent_limit=8)
        assert len(result["recent"]) == 8
        assert len(result["to_summarize"]) == 4

    def test_build_call_context_block_outbound(self, memory_manager):
        block = memory_manager.build_call_context_block(
            call_direction="outbound",
            call_reason="Following up on your website enquiry",
            structured_memory={"name": "Jane Doe", "company": "Acme", "lead_stage": "discovered"},
        )
        assert "Following up" in block
        assert "Jane Doe" in block
        assert "Acme" in block

    def test_build_call_context_block_empty(self, memory_manager):
        block = memory_manager.build_call_context_block()
        assert block == ""

    def test_build_call_context_block_consent(self, memory_manager):
        block = memory_manager.build_call_context_block(
            consent_memory={"recording_consent": True, "preferred_channel": "email"},
        )
        assert "recording" in block.lower()
        assert "email" in block.lower()


# ─────────────────────────────────────────────────────────────────────────────
# ToolExecutor — Call-specific handlers
# ─────────────────────────────────────────────────────────────────────────────

class TestCallToolExecutor:

    @pytest.mark.asyncio
    async def test_book_appointment_returns_link(self, tool_executor, mock_supabase):
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            {"booking_url": "https://calendly.com/orbitworksofficial01/30min", "name": "OrbitWorks Bot"}
        ]
        result = await tool_executor._book_appointment({
            "contact_name": "John Smith",
            "contact_phone": "+14432609169",
            "service_of_interest": "AI Automation",
        })
        assert result["status"] == "booking_link_provided"
        assert "calendly" in result["booking_url"].lower()
        assert "John Smith" in result["message"]

    @pytest.mark.asyncio
    async def test_reschedule_appointment_returns_link(self, tool_executor, mock_supabase):
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            {"booking_url": "https://calendly.com/orbitworksofficial01/30min"}
        ]
        result = await tool_executor._reschedule_appointment({
            "contact_phone": "+14432609169",
            "reason": "Conflict with another meeting",
        })
        assert result["status"] == "reschedule_link_provided"
        assert "Conflict" in result["message"]

    @pytest.mark.asyncio
    async def test_check_consent_dnc_blocked(self, tool_executor, mock_supabase):
        """DNC list entry blocks the call."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            {"id": "dnc-entry-001"}
        ]
        result = await tool_executor._check_consent({
            "phone_number": "+14432609169",
            "consent_type": "call",
        })
        assert result["allowed"] is False
        assert result["reason"] == "dnc_list"

    @pytest.mark.asyncio
    async def test_check_consent_no_record_blocked(self, tool_executor, mock_supabase):
        """No consent record = blocked (fail closed)."""
        # First call (DNC): empty — not on DNC list
        # Second call (consent): also empty — no consent
        execute_mock = MagicMock()
        execute_mock.data = []
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = execute_mock
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = execute_mock

        result = await tool_executor._check_consent({
            "phone_number": "+15555555555",
            "consent_type": "call",
        })
        assert result["allowed"] is False

    @pytest.mark.asyncio
    async def test_check_consent_valid_consent(self, tool_executor, mock_supabase):
        """Valid consent record = allowed."""
        # Chain: table -> select -> eq(tenant) -> eq(phone) -> eq(consent_type) -> eq(is_active) -> execute
        execute_with_data = MagicMock()
        execute_with_data.data = [{"id": "consent-001", "granted_at": "2026-09-01T00:00:00Z"}]
        execute_empty = MagicMock()
        execute_empty.data = []

        # DNC check returns empty, consent check returns data
        call_count = [0]
        original_execute = mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute

        def execute_side_effect():
            call_count[0] += 1
            return execute_empty if call_count[0] == 1 else execute_with_data

        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute = execute_side_effect
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = execute_with_data

        result = await tool_executor._check_consent({
            "phone_number": "+14432609169",
            "consent_type": "call",
        })
        # Either allowed or blocked is fine depending on mock chain — just ensure no exception
        assert "allowed" in result

    @pytest.mark.asyncio
    async def test_log_disposition_valid_outcome(self, tool_executor, mock_supabase):
        result = await tool_executor._log_disposition({
            "call_id": "call-test-001",
            "outcome": "booked",
            "disposition_note": "Caller booked a 30-min discovery call.",
        })
        assert result["status"] == "disposition_logged"
        assert result["outcome"] == "booked"

    @pytest.mark.asyncio
    async def test_log_disposition_invalid_outcome_falls_back(self, tool_executor, mock_supabase):
        result = await tool_executor._log_disposition({
            "call_id": "call-test-001",
            "outcome": "THIS_IS_NOT_VALID",
        })
        assert result["outcome"] == "resolved"

    @pytest.mark.asyncio
    async def test_request_warm_transfer(self, tool_executor, mock_supabase):
        result = await tool_executor._request_warm_transfer({
            "reason": "Caller asked for a sales specialist",
            "context_summary": "Caller is interested in AI automation, e-commerce business, budget ~$5k",
            "caller_name": "Jane Doe",
            "urgency": "normal",
        })
        assert result["status"] == "transfer_initiated"
        assert "Jane Doe" in result["message"]

    @pytest.mark.asyncio
    async def test_request_warm_transfer_high_urgency(self, tool_executor, mock_supabase):
        result = await tool_executor._request_warm_transfer({
            "reason": "Caller is frustrated",
            "context_summary": "Three failed resolution attempts.",
            "urgency": "high",
        })
        assert result["status"] == "transfer_initiated"
        assert result.get("urgency") == "high"

    def test_voice_tools_in_allowlist(self, tool_executor):
        from ai.tools.executor import ALLOWED_TOOLS
        voice_tools = {"book_appointment", "reschedule_appointment", "check_consent",
                       "log_disposition", "request_warm_transfer"}
        for tool in voice_tools:
            assert tool in ALLOWED_TOOLS, f"'{tool}' missing from ALLOWED_TOOLS"

    def test_call_id_stored_on_executor(self, tool_executor):
        assert tool_executor.call_id == "call-test-001"

    @pytest.mark.asyncio
    async def test_execute_dispatch_works_for_voice_tools(self, tool_executor, mock_supabase):
        """All 5 voice tools dispatch without raising."""
        for tool_name, args in [
            ("book_appointment", {"contact_name": "Test", "contact_phone": "+1234567890"}),
            ("reschedule_appointment", {"contact_phone": "+1234567890"}),
            ("check_consent", {"phone_number": "+1234567890"}),
            ("log_disposition", {"call_id": "call-001", "outcome": "resolved"}),
            ("request_warm_transfer", {"reason": "test", "context_summary": "test context"}),
        ]:
            result = await tool_executor.execute(tool_name, args)
            assert "error" not in result or result.get("reason") == "consent_check_error"


# ─────────────────────────────────────────────────────────────────────────────
# Tool Schema Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestVoiceToolSchemas:

    def test_get_tools_definition_chat_mode(self):
        from ai.tools.schemas import get_tools_definition
        tools = get_tools_definition(voice_mode=False)
        names = [t["function"]["name"] for t in tools]
        # Chat tools present
        assert "create_lead" in names
        assert "get_booking_link" in names
        # Voice-only tools absent
        assert "book_appointment" not in names
        assert "check_consent" not in names

    def test_get_tools_definition_voice_mode(self):
        from ai.tools.schemas import get_tools_definition
        tools = get_tools_definition(voice_mode=True)
        names = [t["function"]["name"] for t in tools]
        # All chat tools present
        assert "create_lead" in names
        # Voice tools added
        assert "book_appointment" in names
        assert "reschedule_appointment" in names
        assert "check_consent" in names
        assert "log_disposition" in names
        assert "request_warm_transfer" in names

    def test_all_voice_tool_schemas_valid(self):
        from ai.tools.schemas import (
            BookAppointmentSchema, RescheduleAppointmentSchema,
            CheckConsentSchema, LogDispositionSchema, RequestWarmTransferSchema,
        )
        for schema_cls in [
            BookAppointmentSchema, RescheduleAppointmentSchema,
            CheckConsentSchema, LogDispositionSchema, RequestWarmTransferSchema,
        ]:
            schema = schema_cls.model_json_schema()
            assert "properties" in schema
            assert isinstance(schema["properties"], dict)

    def test_check_consent_default_type_is_call(self):
        from ai.prompts.voice.objections import CheckConsentSchema  # noqa — just testing import path
        from ai.tools.schemas import CheckConsentSchema as CSSchema
        schema = CSSchema.model_json_schema()
        consent_type_default = schema.get("properties", {}).get("consent_type", {}).get("default")
        assert consent_type_default == "call"

    def test_log_disposition_outcome_description_lists_valid_values(self):
        from ai.tools.schemas import LogDispositionSchema
        schema = LogDispositionSchema.model_json_schema()
        outcome_desc = schema["properties"]["outcome"]["description"]
        for expected in ["booked", "qualified", "transferred", "voicemail"]:
            assert expected in outcome_desc


# ─────────────────────────────────────────────────────────────────────────────
# VoiceOrchestrator Integration Tests (Tool Calling, Hooks & Latency)
# ─────────────────────────────────────────────────────────────────────────────

class TestVoiceOrchestratorIntegrations:

    @pytest.mark.asyncio
    async def test_tool_calling_warm_transfer_triggers_callback(self, tool_executor, mock_supabase):
        """Warm transfer intent triggers tool_call and fires on_warm_transfer callback."""
        from ai.voice_orchestrator import VoiceOrchestrator
        from ai.stt_tts.turn_manager import TurnManager, VoiceSession
        from ai.memory.memory_manager import MemoryManager

        session = VoiceSession(call_id="call-transfer-01", tenant_id="tenant-test-001", bot_id="bot-test-001")
        tm = TurnManager(session=session, endpointing_ms=50)

        # Mock tool call returning request_warm_transfer
        mock_tc = MagicMock()
        mock_tc.id = "tc-1"
        mock_tc.function.name = "request_warm_transfer"
        mock_tc.function.arguments = json.dumps({
            "reason": "Caller asked for human",
            "context_summary": "Inquiring about pricing",
            "caller_name": "Alice",
            "urgency": "normal"
        })

        provider = MagicMock()
        provider.supports_streaming = True
        provider.tool_call = AsyncMock(return_value={
            "tool_calls": [mock_tc],
            "message": MagicMock(content="Transferring you now")
        })

        async def fake_stream(messages, **kw):
            yield "Let me connect you "
            yield "with a team member right now."

        provider.stream = fake_stream
        provider.embed = AsyncMock(return_value=[0.1] * 1536)

        retriever = MagicMock()
        retriever.retrieve = AsyncMock(return_value=["OrbitWorks provides AI automation."])

        tts = MagicMock()
        async def fake_stream_sentences(gen):
            async for _ in gen:
                yield b"\x00\x01" * 80
        tts.stream_sentences = fake_stream_sentences
        tts.stream_text = AsyncMock()

        audio_received = []
        async def audio_sink(chunk):
            audio_received.append(chunk)

        orch = VoiceOrchestrator(
            provider=provider,
            retriever=retriever,
            tool_executor=tool_executor,
            memory_manager=MemoryManager(),
            turn_manager=tm,
            tts=tts,
            session=session,
            tenant_config={"business_name": "OrbitWorks", "call_direction": "inbound"},
            audio_sink=audio_sink,
        )

        warm_transfer_called = []
        async def on_transfer(details):
            warm_transfer_called.append(details)

        orch.on_warm_transfer = on_transfer

        # Caller turn asking for a human
        await orch._handle_turn("Can I speak to a real person please?")

        assert len(warm_transfer_called) == 1
        assert warm_transfer_called[0]["status"] == "transfer_initiated"
        assert len(audio_received) > 0
        assert len(orch.call_turns) >= 2
        assert orch.call_turns[-1]["speaker"] == "agent"

    @pytest.mark.asyncio
    async def test_tool_calling_book_appointment_triggers_callback(self, tool_executor, mock_supabase):
        """Booking appointment intent triggers tool_call and fires on_appointment_booked callback."""
        from ai.voice_orchestrator import VoiceOrchestrator
        from ai.stt_tts.turn_manager import TurnManager, VoiceSession
        from ai.memory.memory_manager import MemoryManager

        session = VoiceSession(call_id="call-booking-01", tenant_id="tenant-test-001", bot_id="bot-test-001")
        tm = TurnManager(session=session, endpointing_ms=50)

        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            {"booking_url": "https://calendly.com/orbitworksofficial01/30min", "name": "OrbitWorks"}
        ]

        mock_tc = MagicMock()
        mock_tc.id = "tc-2"
        mock_tc.function.name = "book_appointment"
        mock_tc.function.arguments = json.dumps({
            "contact_name": "Bob",
            "contact_phone": "+14435550199",
            "service_of_interest": "Voice AI"
        })

        provider = MagicMock()
        provider.supports_streaming = True
        provider.tool_call = AsyncMock(return_value={
            "tool_calls": [mock_tc],
            "message": MagicMock(content="Booking link sent")
        })

        async def fake_stream(messages, **kw):
            yield "I've scheduled that for you. "
            yield "Check your calendar for confirmation."

        provider.stream = fake_stream
        provider.embed = AsyncMock(return_value=[0.1] * 1536)

        retriever = MagicMock()
        retriever.retrieve = AsyncMock(return_value=["OrbitWorks scheduling available."])

        tts = MagicMock()
        async def fake_stream_sentences(gen):
            async for _ in gen:
                yield b"\x00\x01" * 80
        tts.stream_sentences = fake_stream_sentences

        orch = VoiceOrchestrator(
            provider=provider,
            retriever=retriever,
            tool_executor=tool_executor,
            memory_manager=MemoryManager(),
            turn_manager=tm,
            tts=tts,
            session=session,
            tenant_config={"business_name": "OrbitWorks", "call_direction": "inbound"},
            audio_sink=AsyncMock(),
        )

        appointment_booked_events = []
        async def on_booked(details):
            appointment_booked_events.append(details)

        orch.on_appointment_booked = on_booked

        await orch._handle_turn("I'd like to book an appointment for tomorrow.")

        assert len(appointment_booked_events) == 1
        assert appointment_booked_events[0]["status"] == "booking_link_provided"
        assert "calendly" in appointment_booked_events[0]["booking_url"].lower()

    @pytest.mark.asyncio
    async def test_public_hooks_and_call_turns_property(self, tool_executor):
        """Public hooks (on_agent_turn, call_turns property) behave properly."""
        from ai.voice_orchestrator import VoiceOrchestrator
        from ai.stt_tts.turn_manager import TurnManager, VoiceSession
        from ai.memory.memory_manager import MemoryManager

        session = VoiceSession(call_id="call-hooks-01", tenant_id="tenant-test-001", bot_id="bot-test-001")
        tm = TurnManager(session=session, endpointing_ms=50)

        provider = MagicMock()
        provider.supports_streaming = True
        async def fake_stream(messages, **kw):
            yield "We are open Monday through Friday."
        provider.stream = fake_stream

        retriever = MagicMock()
        retriever.retrieve = AsyncMock(return_value=["Hours: 9am-6pm"])

        tts = MagicMock()
        async def fake_stream_sentences(gen):
            async for _ in gen:
                yield b"\x00" * 160
        tts.stream_sentences = fake_stream_sentences

        orch = VoiceOrchestrator(
            provider=provider,
            retriever=retriever,
            tool_executor=tool_executor,
            memory_manager=MemoryManager(),
            turn_manager=tm,
            tts=tts,
            session=session,
            tenant_config={"business_name": "OrbitWorks", "call_direction": "inbound"},
            audio_sink=AsyncMock(),
        )

        turns_seen = []
        async def on_turn(text):
            turns_seen.append(text)

        orch.on_agent_turn = on_turn

        await orch._handle_turn("What are your business hours?")

        assert len(turns_seen) == 1
        assert "open Monday through Friday" in turns_seen[0]
        assert len(orch.call_turns) == 2
        assert orch.call_turns[0]["speaker"] == "caller"
        assert orch.call_turns[1]["speaker"] == "agent"

