import os
import asyncio
import dotenv
from supabase import create_client
from ai.providers.openai import OpenAIProvider
from ai.rag.guardrails import GroundingGuardrails
from ai.prompts.templates import compile_system_prompt

dotenv.load_dotenv()

async def test_flow():
    print("--- Starting End-to-End Chatbot RAG Integration Test ---")
    
    # 1. Initialize Clients
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")
    supabase = create_client(supabase_url, supabase_key)
    
    provider = OpenAIProvider()
    guardrails = GroundingGuardrails(confidence_threshold=0.6)
    
    # 2. Setup mock data
    business_name = "Orbit Works"
    personality = "You are a helpful customer support agent for Orbit Works. We offer custom software dev."
    retrieved_knowledge = "Orbit Works software development services start at $5,000. Operating hours are 9 AM to 5 PM."
    
    # User query
    user_query = "How much do your services cost?"
    print(f"\nUser Query: {user_query}")
    
    # 3. Guardrails input validation
    if not guardrails.is_safe_input(user_query):
        print("Input blocked by guardrails.")
        return
        
    # Generate live embeddings (Proves embedding function is wired in)
    print("Generating query embedding...")
    embedding = await provider.embed(user_query)
    print(f"Generated Embedding successfully! Vector size: {len(embedding)}")
    
    # Check grounding relevance score threshold (exercises meets_grounding_threshold)
    mock_retrieved_chunks = [
        {"content": retrieved_knowledge, "score": 0.85}
    ]
    if not guardrails.meets_grounding_threshold(mock_retrieved_chunks):
        print("Ungrounded search. Abstaining from answering.")
        return
    else:
        print("Grounding threshold check passed!")
        
    # Compile prompt
    system_prompt = compile_system_prompt(
        business_name=business_name,
        business_context=personality,
        retrieved_knowledge=retrieved_knowledge
    )
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_query}
    ]
    
    # 4. Generate Completion using OpenAI
    print("Calling model (gpt-5.6-luna)...")
    try:
        response = await provider.generate(messages, model="gpt-5.6-luna")
        print("\nModel Response:")
        print(response["text"])
        
        # 5. Guardrails output validation
        is_grounded = guardrails.verify_response_grounded(response["text"], [retrieved_knowledge])
        print(f"\nGrounding verification passed? {is_grounded}")
        
    except Exception as e:
        print(f"Error during execution: {e}")

if __name__ == "__main__":
    asyncio.run(test_flow())


