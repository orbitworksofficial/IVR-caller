# Voice Channel Replication & Client Onboarding Guide (Phase 4)

**Author:** Hammad Ahmed (AI/ML Engineer / Voice Architecture Owner)  
*AI IVR Caller — Implementation Plan Phase 4 Exit Documentation*

---

## 1. Overview & Architecture

Per Section 13 of the *Orbit Works AI IVR Caller Implementation Plan*:

> **Phase 4 Exit Criterion:** A new client can be onboarded onto the voice channel as a pure configuration exercise — phone number, voice profile, calling rules, voice script/objections, and knowledge base — **without application-code changes**.

The voice layer utilizes the **"Database Values Win"** configuration pattern inherited from the chatbot platform.

```
       Tenant Database (Supabase)
       ├── ivr_bot_configs
       │     ├── voice_script (JSONB overrides)
       │     ├── abstain_phrase / filler_phrase
       │     └── calling_window / timezone / max_attempts
       ├── voice_profiles (TTS voice & model)
       ├── knowledge_chunks (Grounding & FAQs)
       └── phone_numbers (E.164 Twilio routing)
                      │
                      ▼
       [ TenantConfigResolver + resolve_voice_script ]
                      │
                      ▼
             VoiceOrchestrator
```

---

## 2. Onboarding a Client Without Code Changes

To onboard a new tenant (e.g., `Acme Healthcare` or `Metro Real Estate`):

### Step 1: Ingest Client Knowledge Base
Ingest the client's source materials using the existing knowledge ingestion pipeline:
```bash
python scripts/seed_knowledge.py --tenant-id <TENANT_UUID> --source data/client_kb.pdf
```
Ensure FAQ, pricing, and policy chunks are stored under the client's `tenant_id`.

### Step 2: Configure Voice Profile (TTS)
Create an ElevenLabs voice profile row in `voice_profiles`:
```sql
INSERT INTO voice_profiles (tenant_id, name, provider, voice_id, model_id, is_default)
VALUES (
    '<TENANT_UUID>',
    'Professional Warm Female',
    'elevenlabs',
    'EXAVITQu4vr4xnSDxMaL',
    'eleven_flash_v2_5',
    TRUE
);
```

### Step 3: Configure Inbound Phone Number
Map their provisioned Twilio phone number:
```bash
python scripts/add_phone_number.py --tenant-id <TENANT_UUID> --bot-id <BOT_UUID> --number +14435550199
```

### Step 4: Configure `voice_script` in `ivr_bot_configs`
Populate `ivr_bot_configs.voice_script` with tenant-specific dialogue parameters:

```json
{
  "greetings": {
    "inbound_support": "Thank you for calling Acme Healthcare. I am your AI assistant. How may I direct your call?",
    "inbound_sales": "Hello! Welcome to Acme Healthcare. What services can we help you explore today?"
  },
  "abstain_phrase": "I do not have access to that specific medical or scheduling detail. Let me connect you with our patient coordinator.",
  "transfer_phone": "+14435550150",
  "call_model": "gpt-5.6-luna",
  "hard_abstain": true,
  "strict_numeric_grounding": true,
  "extra_rules": [
    "Never offer medical diagnoses or drug prescriptions.",
    "For emergency medical situations, instruct caller to hang up and dial 911 immediately."
  ],
  "objections": {
    "PRICE": {
      "counter_template": "We work with most major insurance providers and offer flexible payment plans."
    },
    "TRUST": {
      "counter_template": "Acme Healthcare has served the Baltimore area for over 15 years with board-certified physicians."
    }
  }
}
```

---

## 3. Dynamic Objection Handling Configuration

Tenants can override default objections (`PRICE`, `TIMING`, `TRUST`, `NEED_TO_THINK`, `ALREADY_HAVE_PROVIDER`) or register brand-new categories:

```json
"objections": {
  "INSURANCE_NOT_ACCEPTED": {
    "trigger_phrases": ["do you take my insurance", "in network", "out of network"],
    "acknowledge_template": "That is an important question — insurance coverage is critical.",
    "counter_template": "We accept major PPO plans and provide itemized superbills for reimbursement.",
    "soft_close_template": "Would you like me to have our billing coordinator verify your benefits?"
  }
}
```

The system automatically handles:
- **Attempt 1:** Acknowledge + Counter template.
- **Attempt 2:** Acknowledge + Soft Close template.
- **Attempt 3+:** Ceases pushback to avoid aggravating callers.

---

## 4. Live Call Hardening (Phase 3 Controls)

The following automated guardrails operate continuously during live calls:

1. **Deterministic Prompt Injection Shield:**
   - Any pattern matching role-override, developer mode, or system prompt leaks triggers immediate refusal without invoking the LLM.
2. **Deterministic Verbal Opt-Out:**
   - Phrases like "remove me", "don't call me", or "stop calling" immediately add the caller to the DNC table, revoke consent, and politely terminate without LLM dependency.
3. **Spoken Output Sanitization (`SpokenOutputFilter`):**
   - Automatically converts web URLs into conversational links ("a link we can text or email to you").
   - Strips markdown symbols (`**`, `#`, bullets, emojis).
   - Blocks ungrounded numeric claims (prices, percentages) not present in retrieved knowledge sources.
4. **Mandatory AI Disclosure:**
   - Direct questions ("Are you a real person or an AI?") trigger mandatory first-sentence transparency.

---

## 5. Verification Checklist

To confirm client onboarding without regressions:
```bash
# Run full voice pipeline & hardening test suite
pytest tests/test_voice_pipeline.py tests/test_voice_hardening.py -v
```
All tests should pass with 100% compliance.
