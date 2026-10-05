# Orbit Chatbot - Client Onboarding & Replication Guide

This guide details the procedure for onboarding a new client/tenant onto the Orbit Chatbot platform without modifying any core application code.

---

## 1. Database Configuration (Supabase)

To add a new client, you must insert records into the core multi-tenant tables. Run the following SQL or execute equivalent mutations in the Supabase Dashboard:

### Step 1.1: Register the Tenant
Insert a record into the `tenants` table to generate a new `tenant_id`:
```sql
INSERT INTO tenants (name) 
VALUES ('Client Name LLC') 
RETURNING id;
```

### Step 1.2: Configure the Bot
Using the returned `tenant_id`, insert a configuration record into the `bots` table:
```sql
INSERT INTO bots (
    tenant_id, 
    name, 
    support_mode_enabled, 
    sales_mode_enabled, 
    welcome_message, 
    system_personality, 
    booking_url
) VALUES (
    'YOUR_TENANT_ID',
    'Client Bot Name',
    true, -- Support mode active
    true, -- Sales mode active
    'Hello! How can I help you discover our services today?',
    'You are the AI assistant for Client Name. We specialize in X, Y, Z...',
    'https://calendly.com/client-onboarding'
) RETURNING id;
```

### Step 1.3: Authorize Host Domains
Add the domains where the client's widget is allowed to make requests:
```sql
INSERT INTO bot_domains (bot_id, tenant_id, domain) 
VALUES ('YOUR_BOT_ID', 'YOUR_TENANT_ID', 'clientwebsite.com');
```

---

## 2. Knowledge Ingestion & RAG Prep

To prepare the RAG database for the new client, compile their FAQ list, pricing pages, and policies.

1. **Register Knowledge Source:**
   Create source entries in the `knowledge_sources` database table.
2. **Execute Ingestion Pipeline:**
   Run the `workers/ingestion/pipeline.py` script. The script parses client text files, sanitizes content, breaks them into 400-600 token chunks, calculates embeddings, and saves them to `knowledge_chunks` tagged with the correct `tenant_id`.

---

## 3. Deployment (Injecting the Widget)

Provide the client with their unique, single-line embedding tag to copy paste before their site's `</body>` closure:

```html
<script 
  src="https://chat.orb-itworks.com/widget.js" 
  data-bot-id="YOUR_BOT_ID">
</script>
```

The widget will resolve the `bot_id` against permitted domains, fetch the branding configuration, and connect to the multi-tenant FastAPI backend with isolated conversations.
