import logging
from typing import Any, Dict, List, Optional
from supabase import Client

logger = logging.getLogger(__name__)


class SecurityEnforcedRetrieval:
    """
    Manages RAG knowledge retrieval with strict, non-bypassable tenant isolation.

    Phase 2 additions:
    - category filter support (retrieve only from specific chunk categories)
    - configurable match_threshold per call
    - retrieve_with_fallback() — retries FTS-only if vector search returns nothing
    """

    def __init__(self, supabase_client: Client, default_threshold: float = 0.10):
        self.supabase = supabase_client
        self.default_threshold = default_threshold

    async def retrieve_knowledge(
        self,
        tenant_id: str,
        query_text: str,
        query_embedding: List[float],
        limit: int = 5,
        match_threshold: Optional[float] = None,
        category: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Retrieves knowledge chunks via hybrid vector + keyword search,
        strictly constrained to the given tenant_id.

        Args:
            tenant_id: The tenant whose knowledge base to search. Required.
            query_text: Raw user query for FTS keyword matching.
            query_embedding: Vector embedding of the query for semantic search.
            limit: Maximum number of chunks to return.
            match_threshold: Minimum score to include a result. Falls back to default.
            category: Optional chunk category filter (e.g. 'pricing', 'services').
        """
        if not tenant_id:
            raise ValueError("Security Violation: tenant_id must be explicitly specified.")

        threshold = match_threshold if match_threshold is not None else self.default_threshold

        response = self.supabase.rpc(
            "match_knowledge_chunks",
            {
                "p_tenant_id": tenant_id,
                "p_query_text": query_text,
                "p_query_embedding": query_embedding,
                "p_match_threshold": threshold,
                "p_match_count": limit,
            }
        ).execute()

        results = response.data or []

        # Apply optional category filter post-retrieval
        if category:
            results = [r for r in results if r.get("category") == category]

        # Multi-tenancy post-retrieval validation (hard failsafe)
        for chunk in results:
            retrieved_tenant_id = chunk.get("tenant_id")
            if str(retrieved_tenant_id) != str(tenant_id):
                raise PermissionError(
                    f"Security Exception: Cross-tenant data leakage detected! "
                    f"Expected tenant '{tenant_id}', got '{retrieved_tenant_id}'."
                )

        return results

    async def retrieve_with_fallback(
        self,
        tenant_id: str,
        query_text: str,
        query_embedding: List[float],
        limit: int = 5,
        match_threshold: Optional[float] = None,
        category: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Tries hybrid retrieval first. If it returns no results, falls back to
        FTS-only keyword search at a lower threshold before abstaining.

        Returns:
            {
                "chunks": [...],
                "retrieval_mode": "hybrid" | "fts_fallback" | "empty"
            }
        """
        chunks = await self.retrieve_knowledge(
            tenant_id=tenant_id,
            query_text=query_text,
            query_embedding=query_embedding,
            limit=limit,
            match_threshold=match_threshold,
            category=category,
        )

        if chunks:
            return {"chunks": chunks, "retrieval_mode": "hybrid"}

        # FTS-only fallback: lower threshold and rely only on keyword match
        logger.info(
            f"Hybrid retrieval returned 0 results for tenant '{tenant_id}'. "
            f"Retrying with FTS-only at lower threshold."
        )
        fts_threshold = (match_threshold or self.default_threshold) * 0.5
        fts_response = self.supabase.rpc(
            "match_knowledge_chunks",
            {
                "p_tenant_id": tenant_id,
                "p_query_text": query_text,
                "p_query_embedding": query_embedding,
                "p_match_threshold": fts_threshold,
                "p_match_count": limit,
            }
        ).execute()

        fts_results = fts_response.data or []

        # Tenant isolation check on fallback results too
        for chunk in fts_results:
            if str(chunk.get("tenant_id")) != str(tenant_id):
                raise PermissionError(
                    f"Security Exception: Cross-tenant leakage in FTS fallback. "
                    f"Expected '{tenant_id}', got '{chunk.get('tenant_id')}'."
                )

        if fts_results:
            return {"chunks": fts_results, "retrieval_mode": "fts_fallback"}

        return {"chunks": [], "retrieval_mode": "empty"}


class VoiceRetriever:
    """
    RAG retriever adapter for voice calls (implementation plan §04/§05).
    Computes query embeddings before invoking retrieve_with_fallback, returning
    chunk content strings suitable for voice prompt grounding.
    """

    def __init__(self, provider: Any, retriever: Any):
        self.provider = provider
        self.retriever = retriever

    async def retrieve(self, query: str, tenant_id: str, bot_id: str, top_k: int = 4) -> List[str]:
        embedding = await self.provider.embed(query)
        result = await self.retriever.retrieve_with_fallback(
            tenant_id=tenant_id,
            query_text=query,
            query_embedding=embedding,
            limit=top_k,
        )
        return [c["content"] for c in result.get("chunks", []) if c.get("content")]

