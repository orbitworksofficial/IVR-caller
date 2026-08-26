import os
import asyncio
import dotenv
from supabase import create_client
from ai.providers.openai import OpenAIProvider
from workers.ingestion.pipeline import KnowledgeIngestionPipeline

dotenv.load_dotenv()

# Sample Orbit Works FAQ content (placeholder until Usman provides real content)
SAMPLE_ORBIT_WORKS_CONTENT = """
Orbit Works is a software development agency specializing in custom AI-powered web and mobile applications, enterprise automation, and data analytics solutions.

Services offered by Orbit Works include: Custom Software Development, AI & Machine Learning Integration, Web Application Development, Mobile App Development, API Development & Integration, UI/UX Design, DevOps & Cloud Deployment, and IT Consulting.

Orbit Works software development services start at $5,000 for small projects. Enterprise projects and custom AI integrations are priced from $15,000 upward depending on complexity and scope.

Orbit Works business hours are Monday to Friday, 9:00 AM to 6:00 PM Pakistan Standard Time (PKT). The team is available on WhatsApp and email during these hours.

To book a free strategy consultation with the Orbit Works team, visit our Calendly link provided by your account manager or request it via the chat. Strategy calls are 30 minutes and are completely free.

Orbit Works has delivered over 50 projects across Pakistan, UAE, UK, and USA. Our portfolio includes AI chatbots, SaaS platforms, ERP systems, and e-commerce solutions.

Orbit Works payment terms: 50% upfront before development begins, and 50% upon project delivery. We accept bank transfers, PayPal, and Wise.

The typical delivery timeline for a standard web application at Orbit Works is 4-8 weeks. AI integrations may take 6-12 weeks depending on data readiness.

Orbit Works provides 3 months of free post-launch support for all projects. Extended support packages are available on request.

Orbit Works technology stack includes Python, FastAPI, React, Next.js, Supabase, PostgreSQL, OpenAI APIs, and AWS/GCP cloud platforms.
"""

async def run_ingestion():
    print("--- Orbit Works Knowledge Base Ingestion Script ---")

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")
    supabase = create_client(supabase_url, supabase_key)
    provider = OpenAIProvider()
    pipeline = KnowledgeIngestionPipeline(supabase_client=supabase, chunk_size=80, overlap=15)

    # Step 1: Insert a tenant record if not exists
    print("\n[1/4] Registering tenant...")
    tenant_resp = supabase.table("tenants").insert({"name": "Orbit Works"}).execute()
    tenant_id = tenant_resp.data[0]["id"]
    print(f"Tenant ID: {tenant_id}")

    # Step 2: Insert a bot record
    print("\n[2/4] Registering bot...")
    bot_resp = supabase.table("bots").insert({
        "tenant_id": tenant_id,
        "name": "Orbit Works AI Assistant",
        "welcome_message": "Hi! I'm the Orbit Works AI Assistant. How can I help you today?",
        "system_personality": "You are a helpful, warm, and professional assistant for Orbit Works.",
        "support_mode_enabled": True,
        "sales_mode_enabled": True,
        "booking_url": "https://calendly.com/orbitworks"
    }).execute()
    bot_id = bot_resp.data[0]["id"]
    print(f"Bot ID: {bot_id}")

    # Step 3: Insert a knowledge source
    print("\n[3/4] Registering knowledge source...")
    source_resp = supabase.table("knowledge_sources").insert({
        "tenant_id": tenant_id,
        "bot_id": bot_id,
        "source_name": "Orbit Works FAQ v1",
        "source_type": "faq"
    }).execute()
    source_id = source_resp.data[0]["id"]
    print(f"Source ID: {source_id}")

    # Step 4: Insert a knowledge document
    doc_resp = supabase.table("knowledge_documents").insert({
        "tenant_id": tenant_id,
        "source_id": source_id,
        "title": "Orbit Works General FAQ",
        "metadata": {"version": "1.0", "author": "Hammad Ahmed"}
    }).execute()
    document_id = doc_resp.data[0]["id"]
    print(f"Document ID: {document_id}")

    # Step 5: Run ingestion pipeline
    print("\n[4/4] Ingesting content into knowledge_chunks...")

    async def embed_fn(text: str):
        return await provider.embed(text)

    records = await pipeline.ingest_document(
        tenant_id=tenant_id,
        source_id=source_id,
        document_id=document_id,
        raw_text=SAMPLE_ORBIT_WORKS_CONTENT,
        embedding_provider_fn=embed_fn
    )

    print(f"\n✅ Ingestion complete! {len(records)} chunks saved to Supabase knowledge_chunks.")
    print(f"\nSave these IDs for the integration test:")
    print(f"  TENANT_ID={tenant_id}")
    print(f"  BOT_ID={bot_id}")

if __name__ == "__main__":
    asyncio.run(run_ingestion())
