"""
Luna vs Terra Benchmark — Phase 3
Author: Hammad Ahmed | AI/ML Engineer

Runs 10 representative questions against both models and records:
response quality signal, latency, token count, estimated cost.
Output is a comparison table that finalizes the routing policy.
"""

import os
import asyncio
import time
import dotenv
from typing import Dict, Any, List

dotenv.load_dotenv()

from ai.providers.openai import OpenAIProvider
from ai.optimization.token_optimizer import TokenOptimizer
from ai.prompts.templates import compile_system_prompt
from apps.api.telemetry import MODEL_PRICING

SAMPLE_KNOWLEDGE = """
OrbitWorks offers AI Automation, Digital Marketing (Google/Meta Ads, SEO),
Websites & Software, Cloud & IT (AWS/Azure/GCP), Staff Augmentation, and
AEO/GEO via OrbitScanner. Free 15-min discovery call. Payment: 50% upfront.
"""

# 10 representative questions covering simple → complex spectrum
BENCHMARK_QUESTIONS = [
    ("Simple FAQ",        "What services does OrbitWorks offer?"),
    ("Simple FAQ",        "How do I contact OrbitWorks?"),
    ("Pricing",           "How much does digital marketing cost?"),
    ("Sales Discovery",   "I run an e-commerce store and need more customers. What would you recommend?"),
    ("Complex Multi-Part","We are a SaaS startup. We need SEO, AI automation for customer support, "
                          "and possibly staff augmentation for a dev. What would you prioritise and why?"),
    ("Ambiguous",         "Can you help with marketing?"),
    ("Unknown Info",      "What is OrbitWorks' annual revenue?"),
    ("Consultative",      "We've tried Google Ads before and it didn't work. Why should we try again?"),
    ("Technical",         "What cloud platforms do you support and what's the migration process?"),
    ("Long Complex",      "I need a complete digital transformation: new website, AI chatbot, cloud migration, "
                          "SEO strategy, and a dedicated dev team. Can OrbitWorks handle all of this, "
                          "and what would the engagement look like?"),
]


async def benchmark_question(
    provider: OpenAIProvider,
    optimizer: TokenOptimizer,
    question: str,
    model: str,
) -> Dict[str, Any]:
    """Runs a single question against a model and records metrics."""

    system_prompt = compile_system_prompt(
        business_name="OrbitWorks",
        business_context="Professional AI assistant for OrbitWorks digital agency.",
        retrieved_knowledge=SAMPLE_KNOWLEDGE,
        sales_mode_enabled=True,
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]

    input_tokens_est = optimizer.count_messages_tokens(messages)
    start = time.time()

    try:
        result = await provider.generate(messages, model=model, max_tokens=300)
        latency_ms = int((time.time() - start) * 1000)
        response = result.get("text", "")
        usage = result.get("usage", {})
        input_tok = usage.get("prompt_tokens", input_tokens_est)
        output_tok = usage.get("completion_tokens", optimizer.count_tokens(response))

        rates = MODEL_PRICING.get(model, {"input": 1.0, "output": 3.0})
        cost = (input_tok / 1_000_000) * rates["input"] + (output_tok / 1_000_000) * rates["output"]

        return {
            "model": model,
            "latency_ms": latency_ms,
            "input_tokens": input_tok,
            "output_tokens": output_tok,
            "estimated_cost_usd": round(cost, 8),
            "response_length": len(response),
            "response_preview": response[:120].replace("\n", " "),
            "error": None,
        }
    except Exception as e:
        return {
            "model": model, "latency_ms": -1, "input_tokens": 0,
            "output_tokens": 0, "estimated_cost_usd": 0.0,
            "response_length": 0, "response_preview": f"ERROR: {e}", "error": str(e),
        }


async def run_benchmark():
    print("="*70)
    print("  Luna vs Terra Benchmark — OrbitWorks Chatbot Phase 3")
    print("  Author: Hammad Ahmed | AI/ML Engineer")
    print("="*70)

    provider = OpenAIProvider()
    optimizer = TokenOptimizer()

    results = []
    for i, (category, question) in enumerate(BENCHMARK_QUESTIONS, 1):
        print(f"\n[{i:02d}/{len(BENCHMARK_QUESTIONS)}] {category}: {question[:55]}...")

        luna_result = await benchmark_question(provider, optimizer, question, "gpt-5.6-luna")
        await asyncio.sleep(12)
        terra_result = await benchmark_question(provider, optimizer, question, "gpt-5.6-terra")

        results.append({
            "question_id": f"q{i:02d}",
            "category": category,
            "question": question[:60],
            "luna": luna_result,
            "terra": terra_result,
        })

        luna_lat = luna_result["latency_ms"]
        terra_lat = terra_result["latency_ms"]
        print(f"   Luna:  {luna_lat}ms | {luna_result['input_tokens']} in / {luna_result['output_tokens']} out | ${luna_result['estimated_cost_usd']:.8f}")
        print(f"   Terra: {terra_lat}ms | {terra_result['input_tokens']} in / {terra_result['output_tokens']} out | ${terra_result['estimated_cost_usd']:.8f}")

        if i < len(BENCHMARK_QUESTIONS):
            await asyncio.sleep(15)

    # Summary
    print(f"\n{'='*70}")
    print("  BENCHMARK SUMMARY")
    print(f"{'='*70}")
    print(f"  {'Category':<22} {'Luna ms':>8} {'Terra ms':>9} {'Luna cost':>12} {'Terra cost':>12}")
    print(f"  {'-'*64}")

    luna_total_cost = 0.0
    terra_total_cost = 0.0
    for r in results:
        l, t = r["luna"], r["terra"]
        luna_total_cost += l["estimated_cost_usd"]
        terra_total_cost += t["estimated_cost_usd"]
        lat_diff = "⚡" if l["latency_ms"] < t["latency_ms"] else "  "
        print(f"  {r['category']:<22} {l['latency_ms']:>7}ms {t['latency_ms']:>8}ms "
              f"  ${l['estimated_cost_usd']:.6f}  ${t['estimated_cost_usd']:.6f} {lat_diff}")

    print(f"  {'-'*64}")
    print(f"  {'TOTAL':<22} {'':>8} {'':>9} ${luna_total_cost:.6f}  ${terra_total_cost:.6f}")

    cost_ratio = terra_total_cost / luna_total_cost if luna_total_cost > 0 else 0
    print(f"\n  Terra is {cost_ratio:.1f}x more expensive than Luna for this question set.")
    print(f"\n  📋 Routing Policy Recommendation:")
    print(f"     → Use Luna (gpt-5.6-luna) for: FAQ, support, simple consultative")
    print(f"     → Use Terra (gpt-5.6-terra) for: tool calls, multi-part complex, long context >3000 chars")
    print(f"     → Never upgrade model for unknown facts — abstain instead")
    print(f"\n{'='*70}\n")


if __name__ == "__main__":
    asyncio.run(run_benchmark())
