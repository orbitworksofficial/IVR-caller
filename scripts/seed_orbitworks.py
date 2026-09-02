#!/usr/bin/env python3
"""
Seeds the OrbitWorks tenant, bot and knowledge base.

Unlike seed_knowledge.py (which is hardcoded to a specific PDF), this accepts
any .txt/.md/.pdf file and is safe to re-run: it reuses an existing tenant/bot
of the same name rather than creating duplicates.

    python scripts/seed_orbitworks.py --knowledge knowledge/orbitworks_faq.txt

Uses SUPABASE_SERVICE_KEY because seeding writes across the RLS boundary
before any tenant context exists. That key is server-side only.
"""

import os
import sys
import argparse
import asyncio
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dotenv
dotenv.load_dotenv()

from supabase import create_client
from ai.providers.openai import OpenAIProvider
from workers.ingestion.pipeline import KnowledgeIngestionPipeline

TENANT_NAME = "OrbitWorks"
BOT_NAME = "OrbitWorks AI Assistant"
WELCOME = "Hi! I'm the OrbitWorks assistant. What can I help you with today?"
PERSONALITY = (
    "You are the AI assistant for OrbitWorks, an AI automation and IT services "
    "company headquartered in the United States. OrbitWorks provides AI automation, "
    "digital marketing, IT and cloud solutions, custom software, and staff "
    "augmentation to startups, small and mid-sized businesses, and enterprises."
)
BOOKING_URL = "https://calendly.com/orbitworks/discovery"


def read_content(path: str) -> str:
    """Reads .txt/.md directly, or extracts text from a .pdf."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Knowledge file not found: {path}")

    if path.lower().endswith(".pdf"):
        import pdfplumber
        text = ""
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                text += (page.extract_text() or "") + "\n"
        return text.strip()

    with open(path, encoding="utf-8") as f:
        return f.read().strip()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--knowledge", required=True, help="Path to .txt, .md or .pdf")
    parser.add_argument("--category", default="faq")
    parser.add_argument("--title", default="OrbitWorks FAQ")
    args = parser.parse_args()

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env")
        return 1

    supabase = create_client(url, key)
    provider = OpenAIProvider()

    print("=" * 62)
    print("  OrbitWorks knowledge base seeding")
    print("=" * 62)

    # ── 1. Tenant (reuse if present) ─────────────────────────────────────────
    existing = supabase.table("tenants").select("id").eq("name", TENANT_NAME).execute()
    if existing.data:
        tenant_id = existing.data[0]["id"]
        print(f"[1/5] Tenant exists       {tenant_id}")
    else:
        resp = supabase.table("tenants").insert({"name": TENANT_NAME}).execute()
        tenant_id = resp.data[0]["id"]
        print(f"[1/5] Tenant created      {tenant_id}")

    # ── 2. Bot (reuse if present) ────────────────────────────────────────────
    existing = supabase.table("bots").select("id").eq(
        "tenant_id", tenant_id
    ).eq("name", BOT_NAME).execute()
    if existing.data:
        bot_id = existing.data[0]["id"]
        print(f"[2/5] Bot exists          {bot_id}")
    else:
        resp = supabase.table("bots").insert({
            "tenant_id": tenant_id,
            "name": BOT_NAME,
            "welcome_message": WELCOME,
            "system_personality": PERSONALITY,
            "booking_url": BOOKING_URL,
            "support_mode_enabled": True,
            "sales_mode_enabled": True,
            "tone": "friendly",
            "escalation_email": "sales@orb-itworks.com",
            "escalation_phone": "+1 443 260 9169",
            "business_hours": "Monday to Friday, 9AM to 6PM EST",
            "launcher_label": "Chat with us",
            "status": "active",
        }).execute()
        bot_id = resp.data[0]["id"]
        print(f"[2/5] Bot created         {bot_id}")

    # ── 3. Knowledge source + document ───────────────────────────────────────
    src = supabase.table("knowledge_sources").insert({
        "tenant_id": tenant_id,
        "bot_id": bot_id,
        "name": "OrbitWorks FAQ",
        "source_name": "OrbitWorks FAQ",
        "source_type": "faq",
        "status": "active",
    }).execute()
    source_id = src.data[0]["id"]

    doc = supabase.table("knowledge_documents").insert({
        "tenant_id": tenant_id,
        "source_id": source_id,
        "title": args.title,
        "file_path": args.knowledge,
        "status": "processing",
        "metadata": {"seeded_by": "scripts/seed_orbitworks.py"},
    }).execute()
    document_id = doc.data[0]["id"]
    print(f"[3/5] Document created    {document_id}")

    # ── 4. Read + ingest ─────────────────────────────────────────────────────
    text = read_content(args.knowledge)
    print(f"[4/5] Read {len(text):,} characters from {args.knowledge}")

    # ~600 words per chunk with overlap keeps each FAQ answer intact while
    # staying inside the plan's 400-600 token target.
    pipeline = KnowledgeIngestionPipeline(
        supabase_client=supabase, chunk_size=180, overlap=30
    )

    print("[5/5] Embedding and inserting chunks (this calls the OpenAI API)...")
    records = await pipeline.ingest_document(
        tenant_id=tenant_id,
        source_id=source_id,
        document_id=document_id,
        text=text,
        provider=provider,
        title=args.title,
        category=args.category,
    )

    supabase.table("knowledge_documents").update(
        {"status": "processed"}
    ).eq("id", document_id).execute()

    print()
    print("=" * 62)
    print(f"  Done. {len(records)} chunks indexed.")
    print("=" * 62)
    print()
    print("Add these to your .env:")
    print(f"  TEST_TENANT_ID={tenant_id}")
    print(f"  TEST_BOT_ID={bot_id}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
