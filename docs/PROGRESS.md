# AI IVR Caller — Progress Log

> **Living file.** Update it in the same commit as the work, every time something is built.
> It records what's ✅ built, what's 🔨 in progress, and what's ⏭️ planned next, measured against
> the *Orbit Works AI IVR Caller Implementation Plan v1.0* (24 Sep 2026).
> Owners: **Kashif** = telephony/platform · **Hammad** = voice AI · **Usman** = QA/evals.

**Last updated:** 2026-10-15 by Kashif. Outbound calling with consent/DNC/window enforcement is built (Phase 2). Phase 1's live test call is still waiting on accounts and keys.

---

## Status by phase

| Phase | Status | Exit criterion |
|---|---|---|
| **0. Chatbot platform** (shared brain) | ✅ Live | Chat widget + admin + multi-tenant RAG, deployed on the VPS |
| **1. Foundation** (telephony + basic voice loop) | 🔨 Code complete, live call pending | A developer calls the test number and gets a grounded spoken answer with acceptable latency |
| **2. Voice intelligence & sales** | 🔨 AI side done (Hammad). Platform: outbound + compliance done; warm transfer + Calendly blocked on H9 | Inbound + outbound full flow → booking/follow-up with CRM logging |
| **3. Production hardening & launch** | ⏭️ | Live, stable Orbit Works line |
| **4. Replication & onboarding** | ⏭️ | New client onboarded onto voice by configuration only |

---

## ✅ Built

### Shared platform (chatbot, before voice)
- FastAPI chat API (`/chat`, `/chat/stream`, admin + super-admin routes), Preact widget, Next.js admin
- Supabase: multi-tenant schema, RLS with `WITH CHECK`, hybrid pgvector + FTS search
- LLM provider abstraction + Luna/Terra router, tools, memory, evals
- Docker Compose on the VPS behind nginx, `infra/deploy.sh` with auto-rollback

### Voice AI layer — Hammad (commit `6786918`)
- `ai/stt_tts/`: DeepgramSTT (streaming, 8kHz µ-law), ElevenLabsTTS (sentence streaming), TurnManager (barge-in, endpointing, dead-air filler)
- `ai/voice_orchestrator.py`: per-turn loop (RAG + memory → objection detection → voice prompt → LLM stream → TTS)
- `ai/prompts/voice/`: support/sales/outbound call prompts, objection library with the two-attempt rule
- Call tools (`check_consent`, `log_disposition`, `book_appointment`, `request_warm_transfer`), call memory, latency optimizer, call eval runner
- Migration `0007_call_tables.sql` (calls, call_turns, call_transcripts, phone_numbers, ivr_bot_configs, consent, DNC, campaigns, dial_attempts)

### Voice Gateway — Kashif (2026-10-07, Phase 1)
- **`apps/voice_gateway/`**: a separate FastAPI process (port 8001 → host 18001)
  - `POST /voice/inbound`: Twilio-signature-checked; maps dialled number → tenant/bot, creates the `calls` row, returns TwiML `<Connect><Stream>`
  - `WS /voice/stream`: bidirectional Media Stream. Per-call HMAC token. µ-law ⇄ PCM16 conversion. Feeds DeepgramSTT + TurnManager, plays TTS back. Barge-in sends Twilio `clear`, including while Twilio is still playing buffered audio
  - `POST /voice/status`, `POST /voice/recording-status`: call status/duration and recording references
  - Per-turn **latency logging** (final transcript → first agent audio) to logs and `call_turns.latency_ms`, plus a per-call P90
  - On hang-up or drop it persists `call_turns`, `call_transcripts`, summary, and STT seconds / TTS characters / estimated cost on `calls`
  - Recording disclosure is spoken before the greeting whenever recording is on
- **Migrations moved to the deploy path:** `supabase/migrations/20261007000009_call_tables.sql` (Hammad's 0007, verbatim) and `20261007000010_voice_profiles_and_recordings.sql` (new: `voice_profiles`, `call_recordings`, `ivr_bot_configs.voice_profile_id`)
- **Infra:** `infra/Dockerfile.voice`, Compose service `voice` (opt-in `--profile voice`, so chatbot deploys aren't affected), nginx `location /voice/` with WebSocket upgrade
- **`scripts/add_phone_number.py`**: maps a Twilio number to a tenant's bot
- **Dependency fixes:** added `aiohttp` (ElevenLabsTTS imported it but it wasn't listed), pinned `deepgram-sdk<5` (v5+ removed the API DeepgramSTT uses, so the image would have crashed on the first call)
- **Tests:** `tests/test_voice_gateway.py`, 15 tests including a full simulated call through the real orchestrator. All voice suites: **80/80 pass**. The earlier "35 failures" were only a missing `pytest-asyncio` locally.
- Docs: [VOICE_GATEWAY.md](VOICE_GATEWAY.md) (setup + runbook)

### Outbound calling + compliance — Kashif (2026-10-15, Phase 2)
- **Dial policy** (`apps/voice_gateway/dial_policy.py`): runs immediately before every dial and **fails closed**. Checks E.164 → DNC → active call consent → calling window (tenant timezone, overnight windows supported) → attempt limit (campaign or bot) → retry interval. Every blocked dial is logged in `dial_attempts` as `*_blocked` with the reason.
- **Dialer** (`outbound.py`): uses the tenant's own outbound number, creates `calls` + `dial_attempts` rows, and places the Twilio call with **answering-machine detection** (`DetectMessageEnd`). A Twilio failure marks the call and attempt `failed`.
- **`POST /voice/outbound/dial`**: server-to-server trigger with the `X-Voice-Api-Key` header. Disabled unless `VOICE_DIAL_API_KEY` is set. The model can never dial.
- **`POST /voice/outbound-answer`**: human → same AI pipeline as inbound (outbound prompt + campaign `call_reason`, plus the orchestrator re-checks consent). Machine → the tenant's approved `voicemail_message` only, then hang up. Outcome `voicemail`.
- Status callback now records unanswered outcomes (`busy` / `no_answer` / `failed`) on `dial_attempts`.
- **Verbal opt-out** (`opt_out.py`): "remove me", "don't call me", "stop calling" and similar on any final caller transcript → number added to `dnc_list` (`verbal_request`) and call consent revoked, **even if the caller hangs up instantly**. Doesn't depend on the LLM.
- Migration `20261015000011_outbound_dialing.sql`: `ivr_bot_configs.voicemail_message` + dial-attempt indexes. Added `tzdata` to requirements.
- **`scripts/voice_consent.py`**: grant/revoke consent, add/remove DNC, `check` (dry run), `dial`. A stopgap until the admin UI.
- **Tests:** `tests/test_outbound.py`, 40 tests. All voice suites: **120/120 pass**.

---

## 🔨 In progress — finishing Phase 1 (Kashif)

These need real accounts/keys and can't be done from code alone:
1. [ ] Buy/provision the Twilio test number; set its webhooks (`/voice/inbound`, `/voice/status`)
2. [ ] Add Deepgram / ElevenLabs / Twilio keys + `VOICE_PUBLIC_BASE_URL` to the VPS `.env`
3. [ ] `supabase db push` (call tables + voice profiles/recordings)
4. [ ] `python scripts/add_phone_number.py --number +1…`
5. [ ] `./infra/dc --profile voice up -d --build voice` and reload nginx
6. [ ] **Live test call** → grounded spoken answer; record latency P90 (target < 1000 ms) → Phase 1 exit ✅
7. [ ] Usman: real-call STT accuracy + latency report on 8kHz audio

---

## ⚠️ Handoffs / issues found (need Hammad's review)

Each one is worked around in the gateway without editing `ai/`, but the proper fix belongs in Hammad's code.

| # | Issue | Current state |
|---|---|---|
| H1 | `VoiceOrchestrator` calls `provider.generate(stream=True)`, but `OpenAIProvider.generate` has no streaming path, so **every turn would answer "I'm having trouble…"** | Worked around: `StreamingProviderAdapter` in `apps/voice_gateway/components.py` routes to `provider.stream()`. Should move into `ai/`. |
| H2 | The orchestrator's RAG fallback calls `retrieve_with_fallback` with an **empty embedding**, so calls would never be grounded | Worked around: `VoiceRetriever` embeds first, like `ChatPipeline.retrieve`. Should move into `ai/`. |
| H3 | Gateway hooks latency/barge-in by wrapping the private `_handle_turn` / `_handle_barge_in` and reads `_call_turns` | Works and is tested. Ask for public hooks (`on_agent_turn`, `call_turns` property). |
| H4 | Dead-air filler and the real answer can stream TTS **at the same time** (shared TTS instance, no lock) | Not fixed. May sound like overlapping speech on slow turns; check on live calls. |
| H5 | `latency_cache` expects `prefetch_tenant_config` / `prewarm_faq_embeddings`, which `VoiceSessionCache` doesn't have | Gateway passes `latency_cache=None`. |
| H6 | `log_disposition` writes `ended_at: "now()"` as a string, which Postgres may reject, dropping the outcome update | Gateway sets `ended_at` itself in `finalize_call`. **Verify on the first live call** whether `calls.outcome` is saved. |
| H7 | The orchestrator hardcodes `gpt-5.6-luna` and doesn't use `ModelRouter` | Matches the plan's Luna default. Terra fallback isn't wired. |
| H8 | `SecurityEnforcedRetrieval` / supabase-py are synchronous inside async code, which blocks the event loop for all calls during DB lookups | Fine for Phase 1 volume. Fix before Phase 3 concurrency. |
| H9 | **The orchestrator never runs LLM tool calls**: it only uses `check_consent` / `log_disposition` itself. The AI can't book, transfer or create leads on a call. | **Blocks** Kashif's warm transfer + Calendly booking (Phase 2). Needs tool-calling in `_handle_turn`; then the gateway can act on `request_warm_transfer`. |
| H10 | If the orchestrator's own outbound consent re-check fails, `start()` just returns, leaving **dead air** until the callee hangs up | Rare, since the dial layer checked moments earlier. Better: tell the gateway, which ends the call. |
| H11 | On a verbal opt-out the gateway writes the DNC entry, but the agent should also **say** it (e.g. "Understood, we won't call you again") | Prompt/behaviour change, Hammad's area. |

---

## ⏭️ Planned (Kashif's side, from the plan)

**Phase 2: voice intelligence & consultative sales**
- [ ] Live Calendly booking/reschedule (write access) + confirmation SMS/email. **Blocked on H9** (the AI can't call tools yet).
- [ ] Warm transfer: Twilio call redirect to a human with a context summary (`request_warm_transfer` → REST `<Dial>`). **Blocked on H9.**
- [x] Consent / DNC / calling-window enforcement **at the dial layer** ✅ 2026-10-15
- [x] Outbound dialer service + answering-machine detection → voicemail message / disposition ✅ 2026-10-15
- [x] Opt-out ("remove me") → `dnc_list` immediately ✅ 2026-10-15
- [ ] Campaign runner: works through a `call_campaigns` list on a schedule (calls `place_outbound_call`, so all the policy checks apply)

**Phase 3: production hardening**
- [ ] Copy recordings to Supabase Storage; per-tenant retention (`call_recordings.expires_at`)
- [ ] Sentry + alerts: dropped calls, STT/TTS failures, latency SLO breaches
- [ ] Rate limiting on the gateway; concurrency/scaling (more containers)
- [ ] Per-call cost into `usage_events` (telephony/STT/TTS/LLM tokens)
- [ ] Go live on the Orbit Works main number

**Phase 4: replication**
- [ ] Admin UI: phone numbers, voice profiles, calling windows, consent config
- [ ] Onboard a second test tenant onto voice with no code changes

---

## Changelog

| Date | Who | What |
|---|---|---|
| 2026-10-15 | Kashif | Outbound calling (Phase 2): fail-closed dial policy (consent/DNC/window/attempts/retry), Twilio dialer with AMD + voicemail, dial API, verbal opt-out → DNC, consent CLI, migration 0011, 40 tests (120/120 voice tests pass). Logged H9–H11 for Hammad |
| 2026-10-07 | Kashif | Voice Gateway (Phase 1): Twilio webhooks + Media Stream bridge, call persistence, recording, migrations moved to `supabase/`, Docker/nginx, tests (80/80 voice tests pass), this progress file |
| 2026-09-24 | Hammad | Voice AI layer Phase 1 & 2 (`6786918`) |
