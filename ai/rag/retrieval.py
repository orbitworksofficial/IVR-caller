from typing import List, Dict, Any
from supabase import Client

class SecurityEnforcedRetrieval:
    """Manages RAG knowledge retrieval with strict, non-bypassable tenant isolation filters."""

    def __init__(self, supabase_client: Client):
        self.supabase = supabase_client

    async def retrieve_knowledge(
        self, 
        tenant_id: str, 
        query_text: str, 
        query_embedding: List[float], 
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Retrieves knowledge chunks using hybrid vector + keyword search, strictly constrained by tenant_id.
        
        Note: The actual hybrid merging (combining vector similarity and PostgreSQL Full-Text Search
        via to_tsvector) is offloaded to the database level inside the 'match_knowledge_chunks' RPC 
        for performance and query safety.
        """
        if not tenant_id:
            raise ValueError("Security Violation: tenant_id must be explicitly specified.")

        # Call Supabase stored procedure for hybrid search
        # The database function `match_knowledge_chunks` is expected to filter by tenant_id internally
        response = self.supabase.rpc(
            "match_knowledge_chunks",
            {
                "p_tenant_id": tenant_id,
                "p_query_text": query_text,
                "p_query_embedding": query_embedding,
                "p_match_threshold": 0.5,
                "p_match_count": limit
            }
        ).execute()

        results = response.data or []

        # Multi-tenancy post-retrieval validation check (failsafe)
        for chunk in results:
            retrieved_tenant_id = chunk.get("tenant_id")
            if str(retrieved_tenant_id) != str(tenant_id):
                raise PermissionError(
                    f"Security Exception: Cross-tenant data leakage detected! "
                    f"Expected tenant '{tenant_id}', got chunk for tenant '{retrieved_tenant_id}'."
                )

        return results
