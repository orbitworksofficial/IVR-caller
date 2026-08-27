import os
import asyncio
import dotenv
import pdfplumber
from supabase import create_client
from ai.providers.openai import OpenAIProvider
from workers.ingestion.pipeline import KnowledgeIngestionPipeline

dotenv.load_dotenv()

def extract_pdf_text(pdf_path: str) -> str:
    """Extract all text content from a PDF file."""
    text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
    return text.strip()

# Load real FAQ from the PDF provided by Usman
FAQ_PDF_PATH = "OrbitWorks FAQs.pdf"

async def run_ingestion():
    print("--- Orbit Works Knowledge Base Ingestion Script ---")

    supabase_url = os.getenv("SUPABASE_URL")
    # Seed script uses the service role key to bypass RLS (admin operation).
    # Get it from: Supabase Dashboard → Settings → API → service_role (secret)
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")
    if not os.getenv("SUPABASE_SERVICE_KEY"):
        print("⚠️  Warning: SUPABASE_SERVICE_KEY not set. Falling back to SUPABASE_KEY.")
        print("   RLS may block inserts. Get service_role key from Supabase → Settings → API.\n")
    supabase = create_client(supabase_url, supabase_key)
    provider = OpenAIProvider()
    pipeline = KnowledgeIngestionPipeline(supabase_client=supabase, chunk_size=80, overlap=15)

    # Step 0: Extract real content from PDF
    print(f"\n[0/4] Extracting text from {FAQ_PDF_PATH}...")
    raw_text = extract_pdf_text(FAQ_PDF_PATH)
    print(f"✅ Extracted {len(raw_text)} characters from PDF.")

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
        raw_text=raw_text,
        embedding_provider_fn=embed_fn
    )

    print(f"\n✅ Ingestion complete! {len(records)} chunks saved to Supabase knowledge_chunks.")
    print(f"\nSave these IDs for the integration test:")
    print(f"  TENANT_ID={tenant_id}")
    print(f"  BOT_ID={bot_id}")

if __name__ == "__main__":
    asyncio.run(run_ingestion())
