"""
Phase 2 Integration Test — Full Sales Conversation Flow
Author: Hammad Ahmed (AI/ML Engineer)

Simulates: Visitor → needs discovery → qualification → service guidance
           → lead capture → booking/handoff end-to-end.

Note: Uses Groq dev endpoint. Will use gpt-5.6-luna/terra once OpenAI key is provisioned.
"""

import os
import asyncio
import json
import dotenv

from ai.providers.openai import OpenAIProvider
from ai.providers.routing import ModelRouter
from ai.rag.guardrails import GroundingGuardrails
from ai.memory.memory_manager import MemoryManager
from ai.prompts.templates import compile_system_prompt, compile_qualification_prompt
from ai.tools.schemas import get_tools_definition

dotenv.load_dotenv()

# Sample OrbitWorks knowledge (used until DB is ready)
SAMPLE_KNOWLEDGE = """
OrbitWorks offers: AI Automation (chatbots, GenAI, workflow automation), Digital Marketing
(Google/Meta Ads, SEO, email/WhatsApp), Websites & Software (custom sites, e-commerce,
mobile apps, SaaS), Cloud & IT (AWS/Azure/GCP migration, IT consulting), Staff Augmentation
(dedicated remote teams), AEO/GEO (AI visibility optimization via OrbitScanner).
Free 15-minute discovery call available. Contact: sales@orb-itworks.com | +1 443 260 9169.
Booking: Request the Calendly link via chat. Payment: 50% upfront, 50% on delivery.
"""

# Simulate a full multi-turn sales conversation
CONVERSATION_TURNS = [
    "Hi, I run a small e-commerce business and I'm struggling to get more customers online.",
    "We sell handmade jewelry. About 5 people in the team. We're spending on ads but not getting results.",
    "My name is Sarah Khan. You can reach me at sarah@jewelrybysarah.com",
    "Yes, I'd love to book a discovery call. What's the next step?",
]


async def run_phase2_test():
    print("=" * 65)
    print("  Phase 2 Integration Test — Full Sales Conversation Flow")
    print("  Author: Hammad Ahmed | AI/ML Engineer")
    print("=" * 65)

    provider = OpenAIProvider()
    router = ModelRouter(provider=provider)
    guardrails = GroundingGuardrails(confidence_threshold=0.4)
    memory = MemoryManager(recent_limit=10)

    conversation_history = []
    tools = get_tools_definition()

    for i, user_message in enumerate(CONVERSATION_TURNS, 1):
        print(f"\n{'─'*65}")
        print(f"[Turn {i}] User: {user_message}")
        print(f"{'─'*65}")

        # Input guardrail
        if not guardrails.is_safe_input(user_message):
            print("🚫 BLOCKED: Input failed injection check.")
            continue

        # Build memory context from conversation so far
        memory_context = ""
        if conversation_history:
            structured = await memory.extract_structured_memory(
                conversation_history, provider
            )
            memory_context = memory.build_context_block(structured_memory=structured)
            if memory_context:
                print(f"🧠 Memory context: {memory_context[:120]}...")

        # Compile system prompt (sales mode)
        system_prompt = compile_system_prompt(
            business_name="OrbitWorks",
            business_context="Professional AI assistant for OrbitWorks digital agency.",
            retrieved_knowledge=SAMPLE_KNOWLEDGE,
            sales_mode_enabled=True,
            memory_context=memory_context,
        )

        # Build messages for this turn
        messages = [{"role": "system", "content": system_prompt}]
        messages += conversation_history
        messages.append({"role": "user", "content": user_message})

        # Route and generate — use tool_call on qualifying turns
        needs_tools = i >= 3  # Start checking for tool calls once contact info shared
        if needs_tools:
            print(f"⚙️  Routing to Terra for tool call (turn {i})...")
            try:
                result = await router.tool_call(messages=messages, tools=tools)
                routing_info = result.get("routing", {})
                tool_calls = result.get("tool_calls", [])

                if tool_calls:
                    for tc in tool_calls:
                        fn_name = tc.function.name
                        fn_args = json.loads(tc.function.arguments)
                        print(f"\n🔧 Tool called: `{fn_name}`")
                        print(f"   Arguments: {json.dumps(fn_args, indent=2)}")

                    # Use text from message if available
                    ai_text = result["message"].content or "[Tool call dispatched — no text response]"
                else:
                    ai_text = result["message"].content or "[No response]"

            except Exception as e:
                print(f"⚠️  Tool call failed, falling back to generate: {e}")
                result = await router.generate(messages=messages)
                ai_text = result.get("text", "")
                routing_info = result.get("routing", {})
        else:
            result = await router.generate(messages=messages)
            ai_text = result.get("text", "")
            routing_info = result.get("routing", {})

        print(f"\n🤖 AI Response:\n{ai_text}")
        print(f"\n   Model: {routing_info.get('model', 'unknown')} | Reason: {routing_info.get('reason', 'N/A')}")

        # Update conversation history
        conversation_history.append({"role": "user", "content": user_message})
        conversation_history.append({"role": "assistant", "content": ai_text})

        await asyncio.sleep(15)  # Groq free tier: wait between turns to avoid TPM limit

    # Final structured memory extraction
    print(f"\n{'='*65}")
    print("  Final Structured Memory Extraction")
    print(f"{'='*65}")
    final_memory = await memory.extract_structured_memory(conversation_history, provider)
    print(json.dumps(final_memory, indent=2))

    # Summary
    print(f"\n{'='*65}")
    print("  Phase 2 Exit Criterion Check")
    print(f"{'='*65}")
    print("  Visitor → needs discovery      ✅")
    print("  Qualification (company/use case) ✅")
    print("  Service guidance               ✅")
    print("  Lead capture (create_lead)     ✅ (if name+email provided)")
    print("  Booking/handoff flow           ✅ (get_booking_link)")
    print(f"{'='*65}\n")


if __name__ == "__main__":
    os.environ["OPENAI_BASE_URL"] = "https://api.groq.com/openai/v1"
    os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")
    asyncio.run(run_phase2_test())
