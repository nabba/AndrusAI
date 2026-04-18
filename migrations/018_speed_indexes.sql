-- 018_speed_indexes.sql — Stage 2 speed-upgrade indexes.
--
-- Adds HNSW indexes alongside the existing IVFFlat ones (from 012, 015).
-- HNSW is typically 2-5x faster than IVFFlat for similarity queries at the
-- cost of longer build time and ~4x index size. Keeping both is safe —
-- pgvector picks the best index per query based on statistics.
--
-- Idempotent: uses IF NOT EXISTS so re-applying is a no-op.
-- Run order: can be applied at any time after 012 and 015.
--
-- Apply from within the gateway container (called at startup by
-- app/memory/startup_migrations.py):
--   psql "$MEM0_POSTGRES_URL" -f /app/migrations/018_speed_indexes.sql

SET maintenance_work_mem = '256MB';

-- ── Agent experiences (from migration 012) ──────────────────────────────
CREATE INDEX IF NOT EXISTS agent_experiences_emb_hnsw
  ON agent_experiences USING hnsw (context_embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

-- ── Beliefs (from migration 015) ────────────────────────────────────────
CREATE INDEX IF NOT EXISTS beliefs_emb_hnsw
  ON beliefs USING hnsw (content_embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

-- ── Workspace items (from migration 015) ────────────────────────────────
-- Create only if table exists (defensive — 015 is the source of truth).
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'workspace_items') THEN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'workspace_items' AND column_name = 'content_embedding') THEN
      EXECUTE 'CREATE INDEX IF NOT EXISTS workspace_items_emb_hnsw
               ON workspace_items USING hnsw (content_embedding vector_cosine_ops)
               WITH (m = 16, ef_construction = 64)';
    END IF;
  END IF;
END $$;

-- Session-scope tuning: increase HNSW search recall.
-- Default ef_search = 40 is a good speed/recall trade-off for our workloads.
-- (This is per-session; app code should SET it at connection time.)
