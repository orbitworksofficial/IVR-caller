import re
from typing import List, Dict, Any
from supabase import Client

class KnowledgeIngestionPipeline:
    """Automates clean-up, validation checks, and database ingestion for raw client text files."""

    def __init__(self, supabase_client: Client, chunk_size: int = 500, overlap: int = 50):
        self.supabase = supabase_client
        self.chunk_size = chunk_size
        self.overlap = overlap

    def clean_text(self, text: str) -> str:
        """
        Cleans and normalizes document text by stripping double-spaces and line breaks.
        """
        cleaned = re.sub(r"\s+", " ", text)
        return cleaned.strip()

    def validate_content(self, text: str) -> bool:
        """
        Checks content quality. Rejects empty texts or texts filled with corrupted characters.
        """
        if not text or len(text.strip()) < 20:
            return False # Too short to index
            
        # Basic sanity check: ensure letters exist (not just raw binary or formatting artifacts)
        if not any(c.isalpha() for c in text):
            return False
            
        return True

    def semantic_chunking(self, text: str) -> List[str]:
        """
        Splits clean text into overlapping chunks based on character/approximate token lengths.
        Target: ~400-600 tokens (approx 1600-2400 characters).
        """
        words = text.split(" ")
        chunks = []
        
        i = 0
        while i < len(words):
            # Take a slice of words
            chunk_words = words[i:i + self.chunk_size]
            chunks.append(" ".join(chunk_words))
            
            # Slide index forward, retaining overlap
            i += (self.chunk_size - self.overlap)
            
        return chunks

    async def ingest_document(
        self,
        tenant_id: str,
        source_id: str,
        document_id: str,
        raw_text: str = None,
        embedding_provider_fn: Any = None,
        text: str = None,
        provider: Any = None,
        title: str = None,
        category: str = None,
        source_url: str = None
    ) -> List[Dict[str, Any]]:
        """
        Executes end-to-end ingestion: clean, validate, chunk, embed, and store in knowledge_chunks.
        Supports both text/raw_text parameters and embedding_provider_fn/provider.
        Also inserts optional metadata columns (title, category, source_url) directly.
        """
        actual_text = raw_text or text
        if not actual_text:
            raise ValueError("Ingestion Failed: No text content provided.")

        cleaned = self.clean_text(actual_text)
        if not self.validate_content(cleaned):
            raise ValueError("Ingestion Failed: Document content failed quality validation.")

        chunks = self.semantic_chunking(cleaned)
        inserted_records = []

        for chunk_content in chunks:
            # Generate embedding using the injected function or provider
            if embedding_provider_fn:
                embedding = await embedding_provider_fn(chunk_content)
            elif provider:
                embedding = await provider.embed(chunk_content)
            else:
                raise ValueError("Ingestion Failed: No embedding provider or function provided.")

            payload = {
                "tenant_id": tenant_id,
                "source_id": source_id,
                "document_id": document_id,
                "content": chunk_content,
                "embedding": embedding,
                "title": title,
                "category": category,
                "source_url": source_url,
                "metadata": {}
            }
            
            response = self.supabase.table("knowledge_chunks").insert(payload).execute()
            if response.data:
                inserted_records.extend(response.data)

        return inserted_records
