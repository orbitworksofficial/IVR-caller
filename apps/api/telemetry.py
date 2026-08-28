from typing import Dict, Any
from supabase import Client

# Prices per 1 Million tokens
# Production (OpenAI) prices from implementation plan
# Dev (Groq) prices from console.groq.com/docs/models
MODEL_PRICING = {
    # Production models
    "gpt-5.6-luna":  {"input": 0.20,  "output": 1.20},
    "gpt-5.6-terra": {"input": 2.00,  "output": 12.00},
    # Dev models (Groq) — actual prices
    "llama-3.1-8b-instant":      {"input": 0.05,  "output": 0.08},   # Luna equivalent
    "llama-3.3-70b-versatile":   {"input": 0.59,  "output": 0.79},   # Terra equivalent
    "compound-beta":             {"input": 0.59,  "output": 0.79},   # legacy mapping
}

class TelemetryTracker:
    """Computes running costs and logs LLM usage events to the database."""

    def __init__(self, supabase_client: Client):
        self.supabase = supabase_client

    def calculate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """
        Calculates estimated cost of LLM generation based on model pricing rules.
        """
        rates = MODEL_PRICING.get(model.lower(), {"input": 1.00, "output": 3.00}) # default fallback
        
        input_cost = (input_tokens / 1_000_000) * rates["input"]
        output_cost = (output_tokens / 1_000_000) * rates["output"]
        
        return round(input_cost + output_cost, 6)

    async def log_usage_event(
        self,
        tenant_id: str,
        conversation_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int
    ) -> Dict[str, Any]:
        """
        Saves a usage transaction log directly to the Supabase usage_events table.
        """
        estimated_cost = self.calculate_cost(model, input_tokens, output_tokens)
        
        payload = {
            "tenant_id": tenant_id,
            "conversation_id": conversation_id,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "latency": latency_ms,
            "estimated_cost": estimated_cost
        }
        
        response = self.supabase.table("usage_events").insert(payload).execute()
        return response.data[0] if response.data else {}
