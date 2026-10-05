# Voice Layer — AI IVR Caller

**Author: Hammad Ahmed (AI/ML Engineer / Voice AI Architecture Owner)**
*AI IVR Caller — Phase 1 & 2 | Version 1.0 | 24 September 2026*

One brain, two mouths. The voice layer shares the same RAG/knowledge engine,
LLM provider abstraction, tool engine, tenant model and memory design as the chatbot.
This document covers the components Hammad owns and how they wire together.

---

## Architecture Overview

```
CALLER (PSTN)
    |
Twilio Voice ──(Media Stream / WebSocket)──► Voice Gateway (Kashif)
                                                    |
                               ┌────────────────────┼────────────────────┐
                               |                    |                    |
                          DeepgramSTT          TurnManager          VoiceSession
                        (ai/stt_tts/)        (ai/stt_tts/)          /memory
                               |                    |
                               └─────────► Voice Orchestrator
                                                    |
                               ┌────────────────────┼────────────────────┐
                               |                    |                    |
                          RAG Engine           Tool Engine          MemoryManager
                      (shared w/ chat)      (call-mode tools)      (call methods)
                               |                    |
                               └────────► Model Router (Luna / Terra)
                                                    |
                                         ElevenLabsTTS (ai/stt_tts/)
                                                    |
                            ◄── Audio chunks streamed back to caller ──────┘
```

**Principle**: Kashif's Voice Gateway receives the Twilio Media Stream, passes audio chunks
to `DeepgramSTT`, receives transcripts from `TurnManager`, calls the Voice Orchestrator,
and pumps `ElevenLabsTTS` audio back to the caller. Hammad owns everything below the
Voice Gateway boundary.

---

## Components

### `ai/stt_tts/deepgram.py` — Streaming STT

**Class**: `DeepgramSTT`

Connects to Deepgram Nova via streaming WebSocket. Optimised for Twilio's 8kHz µ-law audio.

```python
stt = DeepgramSTT()

async def process(audio_gen):
    async for text, is_final in stt.stream(audio_gen):
        if is_final:
            # hand off to TurnManager
            await turn_manager.on_stt_result(text, is_final=True)
```

**Key config** (via env):
| Variable | Default | Notes |
|---|---|---|
| `DEEPGRAM_API_KEY` | — | Required |
| `DEEPGRAM_MODEL` | `nova-2-phonecall` | 8kHz-optimised model |

**Latency target**: STT final transcript within **150–300 ms** of caller stopping speech.

---

### `ai/stt_tts/elevenlabs.py` — Streaming TTS

**Class**: `ElevenLabsTTS`

Streams audio from ElevenLabs Flash/Turbo sentence-by-sentence as the LLM generates,
so the caller hears the first word ~150–250 ms after the LLM begins outputting.

```python
tts = ElevenLabsTTS()

# Stream a complete string
async for audio_chunk in tts.stream_text("How can I help you today?", voice_id="..."):
    await send_to_twilio(audio_chunk)

# Stream from LLM token generator (lowest latency path)
async for audio_chunk in tts.stream_sentences(llm_token_gen, voice_id="..."):
    await send_to_twilio(audio_chunk)

# Cancel if caller barge-in detected
tts.cancel()
```

**Key config** (via env):
| Variable | Default | Notes |
|---|---|---|
| `ELEVENLABS_API_KEY` | — | Required |
| `ELEVENLABS_DEFAULT_VOICE_ID` | — | Orbit Works default voice |
| `ELEVENLABS_MODEL_ID` | `eleven_flash_v2_5` | Lowest latency model |

**Output format**: `pcm_8000` (8kHz PCM, direct Twilio playback).

---

### `ai/stt_tts/turn_manager.py` — Turn-taking & Barge-in

**Classes**: `TurnManager`, `VoiceSession`, `CallState`

State machine managing who has the floor on a phone call.

**States**: `IDLE → CALLER_SPEAKING → PROCESSING → AGENT_SPEAKING`

**Critical behaviours**:

| Behaviour | Mechanism |
|---|---|
| **Barge-in** | Caller audio energy > threshold while `AGENT_SPEAKING` → cancel TTS, switch state, emit `on_barge_in` |
| **Endpointing** | After `is_final=True`, start 300–400 ms silence timer. Caller resumes → reset timer. Expires → emit `on_turn_complete(transcript)` |
| **Dead-air protection** | `PROCESSING` state > 2s → emit `on_filler_needed` → agent says "Just a moment…" |
| **Continuation** | Caller utterance within endpointing window is appended to current turn, not treated as new turn |

```python
tm = TurnManager(
    on_turn_complete=handle_turn,
    on_barge_in=cancel_tts,
    on_filler_needed=say_filler,
    endpointing_ms=300,
    dead_air_timeout_ms=2000,
)
```

---

### `ai/prompts/voice/templates.py` — Call-mode System Prompts

Three prompt variants:

| Prompt | Use case |
|---|---|
| `VOICE_SUPPORT_PROMPT` | Inbound support calls |
| `VOICE_SALES_PROMPT` | Inbound/outbound consultative sales |
| `VOICE_OUTBOUND_PROMPT` | Outbound cold/follow-up calls |

**Key voice-specific rules baked in**:
- Max 1-3 sentences per turn (phone delivery)
- No markdown, no bullet lists in spoken responses
- Confirm before acting ("Just to confirm, you'd like me to…")
- AI disclosure on direct ask
- Filler phrases for processing gaps
- Objection handling with two-attempt rule

**Compile via**:
```python
from ai.prompts.voice.templates import compile_voice_prompt

prompt = compile_voice_prompt(
    business_name="OrbitWorks",
    business_context="...",
    retrieved_knowledge="...",
    support_mode_enabled=True,
    sales_mode_enabled=True,
    memory_context="...",
    call_direction="inbound",   # or "outbound"
    call_reason="",             # outbound: why we called
)
```

---

### `ai/prompts/voice/objections.py` — Structured Objection Library

5 objection categories, each with acknowledge/counter/soft-close responses:

| Category | Triggers (examples) |
|---|---|
| `PRICE` | "too expensive", "can't afford", "out of budget" |
| `TIMING` | "not the right time", "maybe later", "next quarter" |
| `TRUST` | "never heard of you", "can I see examples" |
| `NEED_TO_THINK` | "let me think", "need to discuss with partner" |
| `ALREADY_HAVE_PROVIDER` | "already have an agency", "happy with current team" |

**Two-attempt rule** (from brief §07):
```python
from ai.prompts.voice.objections import detect_objection, get_objection_response

obj = detect_objection(caller_text)
if obj:
    response = get_objection_response(obj, attempt_number=1)  # counter
    response = get_objection_response(obj, attempt_number=2)  # soft close
```

**Objections as knowledge chunks** (can be ingested via KB pipeline):
```python
chunks = get_objection_as_knowledge_chunks()
# Returns 5 dicts with category="objection_handling" tag
```

---

### `ai/memory/memory_manager.py` — Call-specific Extensions

New methods added to the existing `MemoryManager`:

| Method | Purpose |
|---|---|
| `extract_call_summary(turns, provider)` | CRM-ready 4-sentence call briefing |
| `get_call_disposition(turns, provider)` | Derives call outcome: `booked|qualified|declined|…` |
| `split_call_turns(turns, recent_limit=8)` | Splits turns for rolling-summary memory (tighter than chat's 10) |
| `build_call_context_block(...)` | Compact context string for voice system prompt injection |

---

### `ai/tools/schemas.py` + `ai/tools/executor.py` — Call-specific Tools

5 new voice tools added. Available when `voice_mode=True`:

| Tool | Trigger | Executor handler |
|---|---|---|
| `book_appointment` | Caller agrees to book | Returns Calendly link (Kashif handles write-access) |
| `reschedule_appointment` | Caller wants to change booking | Returns rebooking link |
| `check_consent` | Before any outbound dial | Checks DNC list + consent_records; **fails closed** |
| `log_disposition` | Call ends | Writes outcome to `calls` table + updates lead stage |
| `request_warm_transfer` | Caller asks for human / frustration detected | Writes handoff, Kashif executes SIP transfer |

**Get voice-mode tool definitions**:
```python
from ai.tools.schemas import get_tools_definition
tools = get_tools_definition(voice_mode=True)  # 11 tools (6 chat + 5 voice)
tools = get_tools_definition(voice_mode=False) # 6 tools (chat only, unchanged)
```

---

### `database/migrations/0007_call_tables.sql` — Call Data Model

New tables (all with `tenant_id` + RLS):

| Table | Purpose |
|---|---|
| `phone_numbers` | Twilio numbers provisioned per tenant |
| `ivr_bot_configs` | Per-bot voice profile (ElevenLabs voice_id, STT model, calling window) |
| `calls` | One row per call: direction, numbers, outcome, cost telemetry |
| `call_turns` | Per-utterance transcript with latency_ms per agent turn |
| `call_transcripts` | Full assembled transcript per call |
| `consent_records` | Outbound call consent (required before every dial) |
| `dnc_list` | Do-not-call entries (checked before every outbound dial) |
| `call_campaigns` | Outbound campaign definitions |
| `dial_attempts` | Per-dial attempt with blocking_reason if consent/DNC/window blocked |

`usage_events` extended with `telephony_minutes`, `stt_seconds`, `tts_characters`, `call_id`.

---

## Latency Budget

Per Section 05 of the implementation plan:

| Stage | Target | How we hit it |
|---|---|---|
| Caller stops → STT final | 150–300 ms | Deepgram Nova endpointing |
| STT final → LLM first token | 300–600 ms | Parallel RAG + memory (asyncio.gather), short prompts |
| LLM first token → TTS first audio | 150–250 ms | Sentence-boundary splitting, stream immediately |
| **Total P90 target** | **< 1,000 ms** | All stages streaming, no blocking waits |

**Techniques**:
- `asyncio.gather` for RAG retrieval + memory context (reuses existing chatbot optimization)
- Sentence-boundary detection: LLM output split at `.`, `!`, `?` → TTS fed sentence-by-sentence
- Tenant config + voice profile pre-fetched at call start (`VoiceSession` cache)
- Call-mode prompts shorter than chat-mode prompts — detail lives in tool results + RAG
- Luna (fast) default; Terra only where evaluation proves it's needed

---

## Running the Evaluation Suite

```bash
# Phase 1 scenarios (7 grounding + disclosure scenarios)
python ai/evaluations/call_eval_runner.py --mode phase1 --verbose

# Phase 2 scenarios (all objections, handoff, sales, consent, booking)
python ai/evaluations/call_eval_runner.py --mode phase2 --verbose

# All phases with JSON report
python ai/evaluations/call_eval_runner.py --mode all --output reports/call_eval_$(date +%Y%m%d).json
```

**Launch acceptance criteria (Section 14 of brief)**:
- ≥ 95% pass rate on approved call evaluation set
- P90 caller-perceived latency < 1,000 ms
- 100% consent/DNC compliance (0 non-compliant dials)
- 100% calls logged with transcript + cost telemetry

---

## Running Unit Tests

```bash
# All voice pipeline tests
pytest tests/test_voice_pipeline.py -v

# Specific class
pytest tests/test_voice_pipeline.py::TestTurnManager -v
pytest tests/test_voice_pipeline.py::TestObjectionLibrary -v
pytest tests/test_voice_pipeline.py::TestCallToolExecutor -v
```

---

## What Kashif Integrates Against

Hammad's components expose these clean integration points for the Voice Gateway:

| Interface | Module | Usage |
|---|---|---|
| `DeepgramSTT.stream(audio_gen)` | `ai.stt_tts.deepgram` | Feed µ-law audio from Twilio; receive `(text, is_final)` tuples |
| `TurnManager.on_audio_chunk(chunk)` | `ai.stt_tts.turn_manager` | Feed PCM chunks for energy/barge-in detection |
| `TurnManager.on_stt_result(text, is_final)` | same | Feed Deepgram transcript events |
| `TurnManager.on_agent_start_speaking()` | same | Notify when TTS begins (arm barge-in) |
| `ElevenLabsTTS.stream_sentences(llm_gen)` | `ai.stt_tts.elevenlabs` | Pipe LLM token stream → audio chunks |
| `ElevenLabsTTS.cancel()` | same | Cancel on barge-in |
| `compile_voice_prompt(...)` | `ai.prompts.voice.templates` | Build system prompt for voice calls |
| `get_tools_definition(voice_mode=True)` | `ai.tools.schemas` | Get 11-tool schema for voice LLM calls |
| `ToolExecutor(call_id=...)` | `ai.tools.executor` | Instantiate with call_id for disposition logging |
| `MemoryManager.build_call_context_block(...)` | `ai.memory.memory_manager` | Build call context block for prompt |

---

## Ownership Boundaries

```
┌─────────────────────────────────────────────────────────┐
│  Kashif owns                                             │
│  • Twilio Voice / Media Streams WebSocket gateway       │
│  • SIP call setup, recording storage                    │
│  • Calendly write-access (booking/reschedule slots)     │
│  • Warm-transfer SIP mechanics (SIP REFER)              │
│  • Consent/DNC enforcement at the dialling layer        │
│  • Docker Compose, deployment, monitoring               │
├─────────────────────────────────────────────────────────┤
│  Hammad owns (this document)                             │
│  • DeepgramSTT, ElevenLabsTTS, TurnManager              │
│  • Voice prompt architecture + objection library        │
│  • Call-specific memory methods                         │
│  • Call-specific tool schemas + handlers                │
│  • Grounding/abstention for voice                       │
│  • Latency budget end-to-end                            │
│  • Call evaluation harness + test suite                 │
├─────────────────────────────────────────────────────────┤
│  Usman owns                                              │
│  • Objection library QA + real-call scoring             │
│  • Expanding eval set to 150+ scenarios                 │
│  • Regression testing after prompt/model changes        │
│  • Launch QA report                                     │
└─────────────────────────────────────────────────────────┘
```

---

*North-star: the AI IVR Caller is not a second product to maintain — it is the same Orbit Works AI brain answering the phone instead of a chat widget.*
