# Voice Gateway — Setup & Runbook

**Owner: Kashif (Telephony & Platform)** · Code: [apps/voice_gateway/](../apps/voice_gateway/)

The gateway is the boundary between Twilio and Hammad's voice layer (see [VOICE.md](VOICE.md)).
It answers calls, streams audio both ways, and persists every call. It runs as its own
process/container, separate from the chat API, sharing the same database and `ai/` code.

```
Caller ─PSTN─► Twilio ──POST /voice/inbound──► gateway: number → tenant/bot, insert calls row,
                  │                                      reply TwiML <Connect><Stream>
                  └──WS /voice/stream (µ-law 8kHz)──► CallSession
                        caller audio ──► DeepgramSTT ──► TurnManager ──► VoiceOrchestrator
                        agent audio  ◄── PCM16→µ-law ◄── ElevenLabsTTS ◄─┘
                  ──POST /voice/status──────────► calls.status / duration
                  ──POST /voice/recording-status► call_recordings
```

## Endpoints

| Route | Caller | Purpose |
|---|---|---|
| `POST /voice/inbound` | Twilio "A call comes in" | Signature-checked. Looks up `phone_numbers` by `To`, creates the `calls` row, returns TwiML that opens the media stream. Unknown number → polite hang-up. |
| `WS /voice/stream` | Twilio Media Streams | Live call. Verifies the per-call HMAC token, builds the pipeline, runs it, persists turns/transcript/cost on hang-up (also on dropped calls). |
| `POST /voice/status` | Twilio status callback | Writes `status`, `duration_s`, `telephony_minutes`. |
| `POST /voice/recording-status` | Twilio recording callback | Stores the recording in `call_recordings` + `calls.recording_url`. |
| `POST /voice/outbound/dial` | Our servers (`X-Voice-Api-Key`) | Places a compliance-checked outbound call. Disabled unless `VOICE_DIAL_API_KEY` is set. |
| `POST /voice/outbound-answer` | Twilio (outbound answer + AMD) | Human → same media stream as inbound. Voicemail → the tenant's approved `voicemail_message`, then hang up. |
| `GET /voice/health` | Docker / monitoring | Liveness. |

## Security

- **Tenant isolation:** tenant and bot come from **our** `phone_numbers` row (by dialled number), then from our `calls` row. Nothing Twilio or the caller sends can choose the tenant. All writes filter on `tenant_id`.
- **Webhooks:** every POST must carry a valid `X-Twilio-Signature`, computed over `VOICE_PUBLIC_BASE_URL + path`. That variable has to match the URL configured in Twilio exactly.
- **Media stream:** the WebSocket has no signed body, so `/voice/inbound` embeds `token = HMAC(TWILIO_AUTH_TOKEN, call_id)` in the TwiML. A stream with a missing or forged token is closed before any AI work starts.
- **Recording:** recording is on by default, and a disclosure is always spoken first (the tenant's `recording_disclosure_text`, or a default notice). Turn it off per bot with `ivr_bot_configs.call_recording_enabled = false`, or globally with `VOICE_RECORDING_ENABLED=false`.

## Outbound calling (Phase 2)

```
trigger ─► dial policy ──blocked──► dial_attempts.outcome = dnc_blocked | consent_blocked | window_blocked | attempts_blocked | retry_blocked
               │ allowed
               ▼
         calls + dial_attempts rows ─► Twilio call with AMD ─► /voice/outbound-answer
                                                  human ─► AI agent (same pipeline as inbound, outbound prompt + campaign call_reason)
                                                  machine ─► approved voicemail_message (or silent hang-up), outcome = voicemail
```

**The dial policy** ([dial_policy.py](../apps/voice_gateway/dial_policy.py)) runs **immediately before every dial** and **fails closed**: a DB error, an unknown timezone or a malformed number all block the dial. Checks, in order:
1. The number is E.164.
2. It is not on the tenant's `dnc_list`.
3. It has an active `consent_records` row (`consent_type='call'`, not revoked).
4. The current time is inside `ivr_bot_configs.calling_window_start/end` in `calling_timezone` (default 09:00–18:00 America/New_York).
5. Fewer than `max_attempts` real dials so far (campaign limit, else bot limit, else 3).
6. At least `retry_interval_hours` since the last dial (default 24).

**Opt-out:** if a caller says "remove me", "don't call me again", "stop calling" and so on, the gateway immediately adds the number to `dnc_list` (`source='verbal_request'`) and revokes call consent. This also works when the caller hangs up straight away. It does not depend on the AI noticing.

**Try it (stopgap CLI until the admin UI):**
```bash
python scripts/voice_consent.py grant +1XXXXXXXXXX --source web_form --evidence "test"
python scripts/voice_consent.py check +1XXXXXXXXXX     # dry run: ALLOWED / BLOCKED + reason
python scripts/voice_consent.py dial  +1XXXXXXXXXX     # real call from the bot's outbound number
```
Or call the API from another server:
```bash
curl -X POST https://chat.orb-itworks.com/voice/outbound/dial -H "X-Voice-Api-Key: $VOICE_DIAL_API_KEY"   -H "Content-Type: application/json" -d '{"tenant_id":"…","bot_id":"…","to_number":"+1…","campaign_id":null}'
```
The bot needs a `phone_numbers` row with direction `outbound` or `both` (`add_phone_number.py` creates `both`). To leave voicemails, set `ivr_bot_configs.voicemail_message`.

## One-time setup

1. **Migrations:** `supabase db push`. This applies `20261007000009_call_tables.sql`, `20261007000010_voice_profiles_and_recordings.sql` and `20261015000011_outbound_dialing.sql`.
2. **Env** (`.env`, see `.env.example`): `DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY`, `ELEVENLABS_DEFAULT_VOICE_ID`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `VOICE_PUBLIC_BASE_URL`.
3. **Map the number to a bot:**
   ```bash
   python scripts/add_phone_number.py --number +1XXXXXXXXXX            # uses TEST_TENANT_ID / TEST_BOT_ID
   ```
4. **Twilio console** → Phone Numbers → your number → Voice configuration:
   - *A call comes in*: Webhook, `POST`, `https://chat.orb-itworks.com/voice/inbound`
   - *Call status changes*: `POST`, `https://chat.orb-itworks.com/voice/status`

## Run

**VPS:**
```bash
./infra/dc --profile voice up -d --build voice    # opt-in profile; plain deploy.sh leaves it alone
sudo cp infra/nginx-chatbot.conf /etc/nginx/sites-available/orbitworks-chatbot && sudo nginx -t && sudo systemctl reload nginx
curl https://chat.orb-itworks.com/voice/health
```
The nginx `location /voice/` block adds the WebSocket upgrade headers and the 1-hour timeouts that live calls need.

**Local dev (with ngrok):**
```bash
uvicorn apps.voice_gateway.main:app --port 8001
ngrok http 8001                       # set VOICE_PUBLIC_BASE_URL to the https ngrok URL, restart uvicorn
```
Point the Twilio number's webhooks at the ngrok URL, then call the number.

**Tests:** `pytest tests/test_voice_gateway.py -v`. This simulates a full call through the real orchestrator, with the vendors stubbed out.

## Watching a call

```bash
docker logs -f orbitworks-voice-gateway | grep -E "Inbound call|live|turn latency|p90"
```
Each agent turn logs `turn latency: N ms`, measured from the caller's final transcript to the first agent audio byte. On hang-up the call logs all its latencies and the P90; the Phase 1/3 target is **under 1000 ms P90**. The per-turn values are also stored in `call_turns.latency_ms`.

```sql
select started_at, from_number, status, duration_s, turn_count, estimated_cost_usd, call_summary
from calls order by started_at desc limit 10;
```
