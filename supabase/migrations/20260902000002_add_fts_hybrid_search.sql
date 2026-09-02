-- Migration 0002: Add Full-Text Search (FTS) support for hybrid retrieval

-- Add a tsvector column for FTS to knowledge_chunks
ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS fts_content TSVECTOR;

-- Create a GIN index for fast keyword search
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_fts ON knowledge_chunks USING GIN(fts_content);

-- Create a trigger to auto-update fts_content whenever content changes
CREATE OR REPLACE FUNCTION update_knowledge_chunk_fts()
RETURNS TRIGGER AS $$
BEGIN
  NEW.fts_content := to_tsvector('english', COALESCE(NEW.content, ''));
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_update_knowledge_chunk_fts ON knowledge_chunks;
CREATE TRIGGER trigger_update_knowledge_chunk_fts
BEFORE INSERT OR UPDATE ON knowledge_chunks
FOR EACH ROW EXECUTE FUNCTION update_knowledge_chunk_fts();

-- Update existing rows (if any) to populate fts_content
UPDATE knowledge_chunks SET fts_content = to_tsvector('english', COALESCE(content, ''));

-- Replace match_knowledge_chunks with a TRUE hybrid (vector + FTS) version.
-- Dropped first: CREATE OR REPLACE cannot alter a function's return type and
-- would fail with SQLSTATE 42P13 if the RETURNS TABLE shape ever changes.
DROP FUNCTION IF EXISTS match_knowledge_chunks(UUID, TEXT, VECTOR(1536), FLOAT, INT);
CREATE OR REPLACE FUNCTION match_knowledge_chunks (
  p_tenant_id UUID,
  p_query_text TEXT,
  p_query_embedding VECTOR(1536),
  p_match_threshold FLOAT,
  p_match_count INT
)
RETURNS TABLE (
  id UUID,
  tenant_id UUID,
  source_id UUID,
  document_id UUID,
  content TEXT,
  title VARCHAR,
  category VARCHAR,
  source_url VARCHAR,
  score FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT
    kc.id,
    kc.tenant_id,
    kc.source_id,
    kc.document_id,
    kc.content,
    kc.title,
    kc.category,
    kc.source_url,
    -- Hybrid score: 70% vector similarity + 30% keyword match boost
    (
      0.7 * (1 - (kc.embedding <=> p_query_embedding)) +
      0.3 * ts_rank(kc.fts_content, plainto_tsquery('english', p_query_text))
    )::FLOAT AS score
  FROM knowledge_chunks kc
  WHERE kc.tenant_id = p_tenant_id
    AND (
      -- At least one search pathway must match
      (1 - (kc.embedding <=> p_query_embedding)) >= p_match_threshold
      OR kc.fts_content @@ plainto_tsquery('english', p_query_text)
    )
  ORDER BY score DESC
  LIMIT p_match_count;
END;
$$;
