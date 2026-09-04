#!/usr/bin/env python3
"""
Usman 300+ Evaluation Runner — Phase 4
Author: Hammad Ahmed | AI/ML Engineer

Parses Usman's QA evaluation PDF (300+ (1).pdf), runs all queries
against the live OpenAI/Supabase RAG pipeline, and generates a detailed report.
"""

import os
import sys
import asyncio
import json
import re
import time
import dotenv
from typing import List, Dict, Any
from dataclasses import dataclass, asdict
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
dotenv.load_dotenv()

from supabase import create_client
from ai.providers.openai import OpenAIProvider
from ai.providers.routing import ModelRouter
from ai.rag.guardrails import GroundingGuardrails
from ai.rag.retrieval import SecurityEnforcedRetrieval
from ai.prompts.templates import compile_system_prompt
from ai.prompts.config import TenantConfigResolver


@dataclass
class TestCase:
    id: str
    category: str
    question: str
    should_abstain: bool = False


@dataclass
class TestResult:
    id: str
    category: str
    question: str
    response: str
    grounded: bool
    abstained: bool
    passed: bool
    latency_ms: int
    chunks_count: int


def parse_usman_pdf(file_path: str) -> List[TestCase]:
    """Parses queries from Usman's PDF or extracted raw text file."""
    if not os.path.exists(file_path):
        if os.path.exists("330_raw.txt"):
            file_path = "330_raw.txt"
        elif os.path.exists("300_raw.txt"):
            file_path = "300_raw.txt"
        else:
            print(f"❌ File not found: {file_path}")
            return []

    if file_path.endswith(".pdf"):
        import pdfplumber
        with pdfplumber.open(file_path) as pdf:
            raw_text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        lines = raw_text.split("\n")
    else:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

    cases = []
    current_category = "General"
    current_question = []
    q_count = 1

    # Simple logic to group by category and merge multi-line questions
    for line in lines:
        line_str = line.strip()
        if not line_str:
            continue

        if line_str.startswith("●"):
            # Section header
            current_category = line_str.replace("●", "").strip()
            continue

        if line_str.startswith("○"):
            if current_question:
                q_text = " ".join(current_question).strip()
                cases.append(TestCase(
                    id=f"usman_{q_count:03d}",
                    category=current_category,
                    question=q_text,
                    should_abstain="refund" in q_text.lower() or "price" in q_text.lower() and "fixed" in q_text.lower()
                ))
                q_count += 1
            current_question = [line_str.replace("○", "").strip()]
        else:
            if current_question:
                current_question.append(line_str)

    # Add last question
    if current_question:
        q_text = " ".join(current_question).strip()
        cases.append(TestCase(
            id=f"usman_{q_count:03d}",
            category=current_category,
            question=q_text,
            should_abstain="refund" in q_text.lower() or "price" in q_text.lower() and "fixed" in q_text.lower()
        ))

    return cases


async def run_evaluation():
    print("="*70)
    print("  Usman 300+ Evaluation Runner — Running on Paid OpenAI Key")
    print("="*70)

    # 1. Initialize variables
    tenant_id = os.getenv("TEST_TENANT_ID")
    bot_id = os.getenv("TEST_BOT_ID")

    if not tenant_id or not bot_id:
        print("❌ ERROR: TEST_TENANT_ID and TEST_BOT_ID must be set in .env")
        return

    # Initialize clients
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")
    supabase = create_client(os.getenv("SUPABASE_URL"), supabase_key)
    provider = OpenAIProvider()
    retriever = SecurityEnforcedRetrieval(supabase_client=supabase)
    guardrails = GroundingGuardrails(confidence_threshold=0.2, abstain_threshold=0.08)
    config_resolver = TenantConfigResolver(supabase_client=supabase)
    router = ModelRouter(provider=provider)

    # Resolve bot configuration once
    bot_config = await config_resolver.get_bot_config(bot_id)

    # Parse test cases
    cases = parse_usman_pdf("300_raw.txt")
    print(f"📊 Parsed {len(cases)} test cases from Usman's document.")

    results: List[TestResult] = []
    passed_count = 0

    # 2. Run cases sequentially with a tiny delay to avoid hitting limits
    for i, case in enumerate(cases, 1):
        print(f"[{i:03d}/{len(cases)}] Running: {case.question[:55]}...")
        start_time = time.time()

        try:
            # 1. Embed query
            query_embedding = await provider.embed(case.question)

            # 2. Retrieve knowledge
            retrieval_result = await retriever.retrieve_with_fallback(
                tenant_id=tenant_id,
                query_text=case.question,
                query_embedding=query_embedding,
                limit=3,
            )
            chunks = retrieval_result["chunks"]
            retrieved_text = "\n\n".join([c["content"] for c in chunks]) if chunks else ""

            # 3. Check hard abstention
            if guardrails.should_abstain(chunks):
                abstained = True
                response_text = "I don't have this specific information in my knowledge base. Please contact our team."
                grounded = True
                passed = True  # Abstaining when we have no data is a PASS
            else:
                abstained = False
                # Compile prompt
                system_prompt = compile_system_prompt(
                    business_name=bot_config.get("name", "OrbitWorks"),
                    business_context=bot_config.get("system_personality", "A helpful AI assistant."),
                    retrieved_knowledge=retrieved_text,
                    support_mode_enabled=bot_config.get("support_mode_enabled", True),
                    sales_mode_enabled=bot_config.get("sales_mode_enabled", True),
                )
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": case.question}
                ]

                # Generate
                api_res = await router.generate(messages=messages, model="gpt-5.6-luna", max_tokens=300)
                response_text = api_res.get("text", "")
                
                # Check grounding
                source_contents = [c["content"] for c in chunks]
                grounded = guardrails.verify_response_grounded(response_text, source_contents)
                
                # Verify passed
                passed = grounded

            latency = int((time.time() - start_time) * 1000)

            if passed:
                passed_count += 1

            results.append(TestResult(
                id=case.id,
                category=case.category,
                question=case.question,
                response=response_text,
                grounded=grounded,
                abstained=abstained,
                passed=passed,
                latency_ms=latency,
                chunks_count=len(chunks)
            ))
            print(f"      → {'✅ PASS' if passed else '❌ FAIL'} | Abstained={abstained} | Latency={latency}ms")

        except Exception as e:
            print(f"      → ❌ ERROR: {e}")
            results.append(TestResult(
                id=case.id,
                category=case.category,
                question=case.question,
                response=f"ERROR: {e}",
                grounded=False,
                abstained=False,
                passed=False,
                latency_ms=int((time.time() - start_time) * 1000),
                chunks_count=0
            ))

        # Sleep briefly between requests to respect concurrent request limits
        await asyncio.sleep(0.5)

    # 3. Print final report summary
    total = len(results)
    pass_rate = (passed_count / total) * 100 if total else 0
    print("\n" + "="*70)
    print("  USMAN EVALUATION RUN COMPLETE")
    print("="*70)
    print(f"  Total Queries Tested: {total}")
    print(f"  Passed:               {passed_count} ({pass_rate:.1f}%)")
    print(f"  Failed:               {total - passed_count}")
    print("="*70)

    # Save to file
    report_file = "ai/evaluations/usman_eval_report.json"
    with open(report_file, "w") as f:
        json.dump([asdict(r) for r in results], f, indent=2)
    print(f"📄 Detailed report saved to: {report_file}\n")


if __name__ == "__main__":
    asyncio.run(run_evaluation())
