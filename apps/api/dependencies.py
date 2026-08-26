from fastapi import Depends
from supabase import Client
from apps.api.supabase_client import get_supabase
from ai.providers.openai import OpenAIProvider
from ai.rag.retrieval import SecurityEnforcedRetrieval
from ai.rag.guardrails import GroundingGuardrails
from ai.memory.memory_manager import MemoryManager

def get_supabase_client() -> Client:
    """Dependency: returns initialized Supabase client."""
    return get_supabase()

def get_ai_provider() -> OpenAIProvider:
    """Dependency: returns initialized OpenAI/LLM provider."""
    return OpenAIProvider()

def get_retriever(supabase: Client = Depends(get_supabase_client)) -> SecurityEnforcedRetrieval:
    """Dependency: returns tenant-safe RAG retriever wired to Supabase."""
    return SecurityEnforcedRetrieval(supabase_client=supabase)

def get_guardrails() -> GroundingGuardrails:
    """Dependency: returns grounding guardrails with default threshold."""
    return GroundingGuardrails(confidence_threshold=0.5)

def get_memory_manager() -> MemoryManager:
    """Dependency: returns conversation memory manager."""
    return MemoryManager(recent_limit=10)
