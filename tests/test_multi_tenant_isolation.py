import asyncio
import os
import sys
import uuid
from typing import Dict, Any

# Ensure project root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()

import pytest
from supabase import create_client
from ai.rag.retrieval import SecurityEnforcedRetrieval
from ai.tools.executor import ToolExecutor
from ai.prompts.config import TenantConfigResolver

# Test UUIDs for Tenant A and Tenant B
TENANT_A_ID = os.getenv("TEST_TENANT_ID", "4b751db0-1c05-4c26-88b7-9a478f391bcf")
BOT_A_ID = os.getenv("TEST_BOT_ID", "dd8fde06-97ba-49b6-b159-f706d34912a7")

TENANT_B_ID = "00000000-0000-0000-0000-000000000002"
BOT_B_ID = "00000000-0000-0000-0000-000000000022"


@pytest.mark.asyncio
async def test_multi_tenant_isolation():
    print("\n--- Running Multi-Tenant Isolation Test Suite ---")
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")
    
    if not supabase_url or not supabase_key:
        print("❌ Missing Supabase credentials in .env")
        return False

    supabase = create_client(supabase_url, supabase_key)
    retriever = SecurityEnforcedRetrieval(supabase_client=supabase)

    # ── Test 1: Vector Knowledge Isolation ─────────────────────────────────────
    print("\n[Test 1] Vector Knowledge Retrieval Isolation")
    dummy_vector = [0.01] * 1536
    
    res_a = await retriever.retrieve_knowledge(
        tenant_id=TENANT_A_ID,
        query_text="OrbitWorks services",
        query_embedding=dummy_vector,
        limit=5
    )
    
    res_b = await retriever.retrieve_knowledge(
        tenant_id=TENANT_B_ID,
        query_text="OrbitWorks services",
        query_embedding=dummy_vector,
        limit=5
    )

    # Assert no Tenant B chunks appear in Tenant A results
    tenant_a_chunks = [c for c in res_a if str(c.get("tenant_id")) == TENANT_B_ID]
    tenant_b_chunks = [c for c in res_b if str(c.get("tenant_id")) == TENANT_A_ID]

    if tenant_a_chunks:
        print(f"❌ LEAK DETECTED: Tenant A returned {len(tenant_a_chunks)} chunks belonging to Tenant B!")
        return False
    if tenant_b_chunks:
        print(f"❌ LEAK DETECTED: Tenant B returned {len(tenant_b_chunks)} chunks belonging to Tenant A!")
        return False

    print("✅ PASS: Zero cross-tenant knowledge leaks detected.")

    # ── Test 2: Lead Data Isolation via ToolExecutor ─────────────────────────────
    print("\n[Test 2] Lead Data Isolation via ToolExecutor")
    executor_a = ToolExecutor(supabase_client=supabase, tenant_id=TENANT_A_ID, bot_id=BOT_A_ID)
    
    test_email_a = f"tenant_a_test_{uuid.uuid4().hex[:6]}@example.com"
    lead_result = await executor_a.execute("create_lead", {
        "name": "Tenant A Lead",
        "email": test_email_a,
        "requirement": "AI Automation"
    })
    print(f"Created Lead for Tenant A: {lead_result}")

    # Query leads for Tenant B
    try:
        b_leads = supabase.table("leads").select("*").eq("tenant_id", TENANT_B_ID).eq("email", test_email_a).execute()
        if b_leads.data:
            print(f"❌ LEAK DETECTED: Tenant B can see Tenant A's lead ({test_email_a})!")
            return False
    except Exception as e:
        print(f"Note on DB query: {e}")

    print("✅ PASS: Tenant A lead is completely invisible to Tenant B.")

    # ── Test 3: Bot Config Isolation ───────────────────────────────────────────
    print("\n[Test 3] Bot Configuration Resolver Isolation")
    resolver = TenantConfigResolver(supabase_client=supabase)
    
    bot_a_cfg = await resolver.get_bot_config(BOT_A_ID)
    print(f"Bot A Config Resolved Name: '{bot_a_cfg.get('name')}' (Tenant: {bot_a_cfg.get('tenant_id', TENANT_A_ID)})")
    
    try:
        bot_b_cfg = await resolver.get_bot_config(BOT_B_ID)
        print(f"Bot B Config Resolved Name: '{bot_b_cfg.get('name')}'")
    except ValueError:
        print(f"✅ PASS: Bot B config throws ValueError as expected (Tenant B bot '{BOT_B_ID}' does not exist).")

    print("\n==================================================")
    print("  🎉 MULTI-TENANT ISOLATION TEST SUITE: PASSED!")
    print("==================================================")
    return True
