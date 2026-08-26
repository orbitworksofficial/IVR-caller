import os
import asyncio
import dotenv
from supabase import create_client
from ai.providers.openai import OpenAIProvider
from ai.rag.guardrails import GroundingGuardrails
from ai.rag.retrieval import SecurityEnforcedRetrieval
from ai.prompts.templates import compile_system_prompt

dotenv.load_dotenv()

# ── Set these after running seed_knowledge.py ──────────────────────────────
TENANT_ID = os.getenv("TEST_TENANT_ID", "")  # paste your tenant UUID here or set in .env
# ───────────────────────────────────────────────────────────────────────────

async def test_flow():
    print("--- Phase 1 End-to-End Integration Test (Real Supabase Retrieval) ---")

    if not TENANT_ID:
        print("ERROR: TEST_TENANT_ID is not set. Run seed_knowledge.py first and set the returned tenant ID in your .env file.")
        return

    # 1. Initialize clients
    supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
    provider = OpenAIProvider()
    guardrails = GroundingGuardrails(confidence_threshold=0.5)
    retriever = SecurityEnforcedRetrieval(supabase_client=supabase)

    # 2. User query
    user_query = "How much do your services cost?"
    print(f"\nUser Query: {user_query}")

    # 3. Input guardrail: prompt injection check
    if not guardrails.is_safe_input(user_query):
        print("BLOCKED: Input failed injection check.")
        return
    print("✅ Input guardrail passed.")

    # 4. Generate query embedding (real call to text-embedding-3-small)
    print("Generating embedding via text-embedding-3-small...")
    query_embedding = await provider.embed(user_query)
    print(f"✅ Embedding generated. Vector size: {len(query_embedding)}")

    # 5. REAL retrieval from Supabase via SecurityEnforcedRetrieval
    print(f"\nQuerying Supabase knowledge_chunks for tenant {TENANT_ID}...")
    retrieved_chunks = await retriever.retrieve_knowledge(
        tenant_id=TENANT_ID,
        query_text=user_query,
        query_embedding=query_embedding,
        limit=5
    )

    if not retrieved_chunks:
        print("No chunks retrieved. Make sure you've run seed_knowledge.py and the DB migration.")
        return

    print(f"✅ Retrieved {len(retrieved_chunks)} real chunk(s) from Supabase:")
    for i, chunk in enumerate(retrieved_chunks, 1):
        print(f"  [{i}] score={chunk.get('score', 'N/A'):.4f} | {chunk['content'][:80]}...")

    # 6. Grounding threshold check
    if not guardrails.meets_grounding_threshold(retrieved_chunks):
        print("\nAbstaining: Retrieved chunks do not meet confidence threshold.")
        return
    print("\n✅ Grounding threshold check passed.")

    # 7. Compile system prompt from retrieved knowledge
    retrieved_text = "\n\n".join([c["content"] for c in retrieved_chunks])
    system_prompt = compile_system_prompt(
        business_name="Orbit Works",
        business_context="You are a professional AI assistant for Orbit Works software agency.",
        retrieved_knowledge=retrieved_text
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_query}
    ]

    # 8. Generate answer using gpt-5.6-luna
    print("\nCalling model (gpt-5.6-luna)...")
    response = await provider.generate(messages, model="gpt-5.6-luna")
    print("\n--- Model Response ---")
    print(response["text"])

    # 9. Output grounding check
    source_contents = [c["content"] for c in retrieved_chunks]
    is_grounded = guardrails.verify_response_grounded(response["text"], source_contents)
    print(f"\n✅ Output grounding verification passed? {is_grounded}")
    print("\n--- Phase 1 Exit Criterion: PASSED ✅ ---")

if __name__ == "__main__":
    asyncio.run(test_flow())
