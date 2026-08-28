#!/usr/bin/env python3
"""
Automated Tenant Onboarding Script — Phase 4
Author: Hammad Ahmed | AI/ML Engineer

Single-command onboarding for a new client tenant.
Covers the full AI onboarding flow without any code changes:

  1. Create tenant record
  2. Create bot with full config
  3. Register knowledge source
  4. Ingest knowledge from PDF or text file
  5. Run knowledge health check
  6. Print summary with IDs to add to .env

Usage:
  python3 scripts/onboard_tenant.py \
    --name "Acme Corp" \
    --knowledge ./acme_faq.pdf \
    --booking-url "https://calendly.com/acme/discovery" \
    --tone friendly \
    --escalation-email support@acme.com

"""

import os
import sys
import asyncio
import argparse
import dotenv

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
dotenv.load_dotenv()

from supabase import create_client
from ai.providers.openai import OpenAIProvider
from ai.evaluations.knowledge_health import KnowledgeHealthChecker
from workers.ingestion.pipeline import KnowledgeIngestionPipeline


def extract_text(file_path: str) -> str:
    """Extracts plain text from a PDF or .txt file."""
    if file_path.endswith(".pdf"):
        try:
            import pdfplumber
            with pdfplumber.open(file_path) as pdf:
                return "\n".join(
                    page.extract_text() or "" for page in pdf.pages
                ).strip()
        except ImportError:
            print("❌ pdfplumber not installed. Run: pip install pdfplumber")
            sys.exit(1)
    elif file_path.endswith(".txt"):
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    else:
        print(f"❌ Unsupported file type: {file_path}. Use .pdf or .txt")
        sys.exit(1)


async def onboard(args):
    print("\n" + "="*60)
    print(f"  OrbitWorks — New Tenant Onboarding")
    print(f"  Client: {args.name}")
    print("="*60)

    # Initialize clients
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")
    if not os.getenv("SUPABASE_SERVICE_KEY"):
        print("⚠️  Warning: Using anon key. RLS may block writes.")
        print("   Set SUPABASE_SERVICE_KEY for reliable onboarding.\n")

    supabase = create_client(os.getenv("SUPABASE_URL"), supabase_key)
    provider = OpenAIProvider()
    pipeline = KnowledgeIngestionPipeline(supabase_client=supabase, chunk_size=80, overlap=15)

    # ── Step 1: Create tenant ────────────────────────────────────────────────
    print("\n[1/5] Creating tenant record...")
    tenant_resp = supabase.table("tenants").insert({
        "name": args.name,
    }).execute()
    tenant_id = tenant_resp.data[0]["id"]
    print(f"  ✅ Tenant ID: {tenant_id}")

    # ── Step 2: Create bot ───────────────────────────────────────────────────
    print("\n[2/5] Creating bot configuration...")
    bot_config = {
        "tenant_id": tenant_id,
        "name": args.name,
        "welcome_message": args.welcome or f"Hi! I'm {args.name}'s AI assistant. How can I help?",
        "booking_url": args.booking_url,
        "support_mode_enabled": not args.sales_only,
        "sales_mode_enabled": not args.support_only,
        "system_personality": args.personality or f"You are a helpful AI assistant for {args.name}.",
        "tone": args.tone,
        "response_language": args.language,
        "escalation_email": args.escalation_email,
        "escalation_phone": args.escalation_phone,
        "business_hours": args.business_hours,
        "topic_restrictions": args.restrict_topics or [],
    }
    bot_resp = supabase.table("bots").insert(bot_config).execute()
    bot_id = bot_resp.data[0]["id"]
    print(f"  ✅ Bot ID: {bot_id}")
    print(f"     Tone: {args.tone} | Language: {args.language}")
    print(f"     Support: {bot_config['support_mode_enabled']} | Sales: {bot_config['sales_mode_enabled']}")

    # ── Step 3: Register knowledge source ────────────────────────────────────
    print("\n[3/5] Registering knowledge source...")
    source_resp = supabase.table("knowledge_sources").insert({
        "tenant_id": tenant_id,
        "bot_id": bot_id,
        "name": f"{args.name} Knowledge Base",
        "source_type": "document",
        "status": "active",
    }).execute()
    source_id = source_resp.data[0]["id"]

    doc_resp = supabase.table("knowledge_documents").insert({
        "tenant_id": tenant_id,
        "source_id": source_id,
        "title": f"{args.name} FAQ",
        "file_path": args.knowledge,
        "status": "processing",
    }).execute()
    document_id = doc_resp.data[0]["id"]
    print(f"  ✅ Source ID:   {source_id}")
    print(f"  ✅ Document ID: {document_id}")

    # ── Step 4: Extract + ingest knowledge ───────────────────────────────────
    print(f"\n[4/5] Extracting and ingesting knowledge from {args.knowledge}...")
    raw_text = extract_text(args.knowledge)
    char_count = len(raw_text)
    print(f"  ✅ Extracted {char_count:,} characters.")

    records = await pipeline.ingest_document(
        tenant_id=tenant_id,
        source_id=source_id,
        document_id=document_id,
        text=raw_text,
        title=f"{args.name} FAQ",
        category=args.category or "faq",
        provider=provider,
    )
    print(f"  ✅ Ingested {len(records)} chunks into knowledge_chunks.")

    # Mark document as processed
    supabase.table("knowledge_documents").update(
        {"status": "processed"}
    ).eq("id", document_id).execute()

    # ── Step 5: Knowledge health check ───────────────────────────────────────
    print("\n[5/5] Running knowledge health check...")
    checker = KnowledgeHealthChecker(supabase_client=supabase)
    health = checker.check(tenant_id)
    checker.print_report(health)

    # ── Summary ───────────────────────────────────────────────────────────────
    print("="*60)
    print(f"  ✅ ONBOARDING COMPLETE — {args.name}")
    print("="*60)
    print(f"\n  Add these to your .env for testing:")
    print(f"  TEST_TENANT_ID={tenant_id}")
    print(f"  TEST_BOT_ID={bot_id}")
    print(f"\n  Health: {health['signal']}")
    print(f"  Chunks: {health['total_chunks']}")
    if health["issues"]:
        print(f"\n  ⚠️  Fix these before going live:")
        for issue in health["issues"]:
            print(f"     • {issue}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="OrbitWorks — Automated Tenant Onboarding (Phase 4)"
    )
    parser.add_argument("--name", required=True, help="Client/business name")
    parser.add_argument("--knowledge", required=True, help="Path to FAQ PDF or .txt file")
    parser.add_argument("--booking-url", help="Calendly or booking link")
    parser.add_argument("--tone", default="professional",
                        choices=["professional", "friendly", "formal"],
                        help="Bot tone (default: professional)")
    parser.add_argument("--language", default="english", help="Response language")
    parser.add_argument("--personality", help="Custom system personality string")
    parser.add_argument("--welcome", help="Custom welcome message")
    parser.add_argument("--escalation-email", help="Escalation contact email")
    parser.add_argument("--escalation-phone", help="Escalation contact phone")
    parser.add_argument("--business-hours", default="Monday–Friday, 9AM–6PM",
                        help="Business hours string")
    parser.add_argument("--category", default="faq", help="Knowledge category tag")
    parser.add_argument("--restrict-topics", nargs="*", help="Topics the bot must not discuss")
    parser.add_argument("--sales-only", action="store_true", help="Disable support mode")
    parser.add_argument("--support-only", action="store_true", help="Disable sales mode")
    args = parser.parse_args()
    asyncio.run(onboard(args))


if __name__ == "__main__":
    main()
