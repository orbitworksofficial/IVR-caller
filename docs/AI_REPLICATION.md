# AI Replication & Configuration Guide
**Author: Hammad Ahmed | AI/ML Engineer | OrbitWorks**

This document is the single source of truth for onboarding a new client tenant
onto the OrbitWorks chatbot platform without any application-code changes.

---

## Overview

The platform is multi-tenant by design. Each client gets:
- A **tenant** record (identity + billing boundary)
- A **bot** record (all AI behavior config — tone, language, modes, escalation)
- A **knowledge base** (their FAQs, services, policies — ingested as vector chunks)

RLS policies enforce that no tenant can ever see another's data.

---

## Prerequisites

Ensure these are set in your `.env` before starting:

```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=sb_secret_...        # admin key — bypasses RLS for onboarding
OPENAI_API_KEY=sk-...                     # production key (1536-dim embeddings)
```

> **Dev mode**: Use `OPENAI_BASE_URL=https://api.groq.com/openai/v1` + `EMBEDDING_PROVIDER=local`
> to run with Groq + local embeddings at zero cost. Re-seed with OpenAI key before going live.

---

## Option A: Automated Onboarding (Recommended)

One command handles everything — tenant, bot, ingestion, health check:

```bash
python3 scripts/onboard_tenant.py \
  --name "Acme Corp" \
  --knowledge ./acme_faq.pdf \
  --booking-url "https://calendly.com/acme/discovery" \
  --tone friendly \
  --escalation-email support@acme.com \
  --business-hours "Mon–Fri, 9AM–5PM EST"
```

### All available options:

| Flag | Default | Description |
|---|---|---|
| `--name` | *required* | Client/business name |
| `--knowledge` | *required* | Path to FAQ `.pdf` or `.txt` |
| `--booking-url` | None | Calendly or booking page URL |
| `--tone` | `professional` | `professional` / `friendly` / `formal` |
| `--language` | `english` | Response language |
| `--personality` | Auto | Custom system personality string |
| `--welcome` | Auto | Custom welcome message |
| `--escalation-email` | None | Urgent contact email |
| `--escalation-phone` | None | Urgent contact phone |
| `--business-hours` | Mon–Fri 9AM–6PM | Business hours string |
| `--category` | `faq` | Knowledge category tag |
| `--restrict-topics` | None | Topics the bot must NOT discuss |
| `--sales-only` | False | Disable support mode |
| `--support-only` | False | Disable sales mode |

The script prints the `TENANT_ID` and `BOT_ID` at the end. Add them to `.env`.

---

## Option B: Manual Steps

If you need more control (e.g., custom ingestion pipeline):

### 1. Insert tenant
```sql
INSERT INTO tenants (name) VALUES ('Acme Corp') RETURNING id;
```

### 2. Insert bot with full config
```sql
INSERT INTO bots (
  tenant_id, name, tone, response_language, welcome_message,
  booking_url, support_mode_enabled, sales_mode_enabled,
  system_personality, escalation_email, business_hours
) VALUES (
  '<tenant_id>', 'Acme Corp', 'friendly', 'english',
  'Hi! How can I help?', 'https://calendly.com/acme',
  true, true,
  'You are a helpful AI assistant for Acme Corp, a SaaS company.',
  'support@acme.com', 'Mon–Fri 9AM–5PM EST'
) RETURNING id;
```

### 3. Ingest knowledge
```bash
# Edit seed_knowledge.py to point to the new FAQ file, or use the onboard script
python3 seed_knowledge.py
```

### 4. Run health check
```bash
TEST_TENANT_ID=<tenant_id> python3 ai/evaluations/knowledge_health.py
```

---

## Bot Config Variables Reference

All values are stored in the `bots` table and loaded at runtime — no code changes needed.

| Column | Type | Effect |
|---|---|---|
| `tone` | `professional`/`friendly`/`formal` | Adjusts response tone |
| `response_language` | string | Forces responses in specified language |
| `max_response_length` | `short`/`medium`/`long` | Controls verbosity |
| `topic_restrictions` | string[] | Topics the bot must refuse |
| `support_mode_enabled` | bool | Enables support/FAQ behavior |
| `sales_mode_enabled` | bool | Enables consultative sales + lead capture |
| `booking_url` | string | Injected into tool calls for booking |
| `escalation_email` | string | Shown when handoff is requested |
| `escalation_phone` | string | Shown when handoff is requested |
| `business_hours` | string | Injected into system prompt context |
| `system_personality` | text | Base personality for the bot |
| `welcome_message` | text | First message shown to widget users |

---

## Production Switch Checklist (Dev → Live)

- [ ] Replace `OPENAI_BASE_URL` (Groq) with real OpenAI key
- [ ] Remove `EMBEDDING_PROVIDER=local`
- [ ] Run SQL to resize vector column: `ALTER TABLE knowledge_chunks DROP COLUMN embedding; ALTER TABLE knowledge_chunks ADD COLUMN embedding VECTOR(1536);`
- [ ] Re-run `python3 seed_knowledge.py` (re-ingests with 1536-dim OpenAI embeddings)
- [ ] Run `python3 ai/evaluations/knowledge_health.py` — confirm 🟢 HEALTHY
- [ ] Run `python3 ai/evaluations/eval_runner.py` — confirm ≥85% pass rate
- [ ] Run `python3 test_integration.py` — confirm Phase 1 exit criterion
- [ ] Run `python3 test_phase2.py` — confirm Phase 2 exit criterion
- [ ] Hit `GET /usage?tenant_id=<id>` — confirm telemetry is logging

---

## Monitoring After Go-Live

```bash
# Check cost and usage for a tenant
curl "https://your-api.com/usage?tenant_id=<id>&limit=50"
```

Response includes:
- `total_cost_usd` — running cost for the last N requests
- `avg_latency_ms` — response time health
- `model_distribution` — Luna vs Terra usage ratio
- `total_input_tokens` / `total_output_tokens`

If Terra usage is > 30% of all calls, review routing thresholds to reduce cost.

---

## Common Issues

| Problem | Likely Cause | Fix |
|---|---|---|
| `RLS policy violation` on insert | Using anon key for admin ops | Use `SUPABASE_SERVICE_KEY` |
| `expected 1536 dimensions, not 384` | Using local embeddings but column is 1536 | Run migration 0005 (resize to 384) |
| `No chunks retrieved` | Embeddings mismatch (384 vs 1536) | Re-seed with the correct embedding provider |
| `rate_limit_exceeded` | Groq free tier TPM | Wait 60s and retry, or upgrade to Groq Dev tier |
| Bot returns wrong answers | Knowledge base thin or misaligned | Run `knowledge_health.py` and re-ingest |

---

*Document maintained by: Hammad Ahmed (AI/ML Engineer)*
*Last updated: Phase 4 — Replication & Client Onboarding*
