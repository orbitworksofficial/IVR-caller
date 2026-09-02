from fastapi import Depends
from supabase import Client
from apps.api.supabase_client import get_supabase, get_supabase_admin
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


def get_supabase_admin_client() -> Client:
    """
    Dependency: service-role Supabase client for the admin API.

    Admin routes must bypass RLS, because the dashboard authenticates with a
    Supabase Auth JWT rather than the `app.current_tenant_id` session variable
    the RLS policies read — so an anon-key query returns nothing.

    This is safe ONLY because get_current_admin has already resolved the caller
    to exactly one tenant via tenant_admins, and every admin route filters its
    queries by that tenant_id. Never use this client for a request whose tenant
    has not been established that way.
    """
    return get_supabase_admin()
