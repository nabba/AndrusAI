-- ============================================================
-- MIGRATION 017: External LLM Ranks
-- Stores ranking signals pulled from third-party leaderboards so
-- the selector can blend them with the in-house telemetry from
-- app.llm_benchmarks. Populated by the llm-external-ranks idle
-- job (see app/llm_external_ranks.py).
-- ============================================================

-- task_type uses '' as the "applies to any task" sentinel instead of
-- NULL, so it can participate in the primary key directly without the
-- COALESCE trick Postgres <15 rejects.
CREATE TABLE IF NOT EXISTS control_plane.external_ranks (
    source        TEXT NOT NULL,                   -- 'openrouter','huggingface','artificial_analysis'
    model_id      TEXT NOT NULL,                   -- catalog key (e.g. 'deepseek-v3.2')
    metric        TEXT NOT NULL,                   -- 'quality','speed','cost','tokens_7d','elo'
    value         NUMERIC(12,4) NOT NULL,          -- normalised 0..1 for quality/speed; raw otherwise
    unit          TEXT,                            -- 'score','tok_s','usd_per_m','tokens','elo'
    task_type     TEXT NOT NULL DEFAULT '',        -- '' = applies to any task
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    raw_payload   JSONB,
    PRIMARY KEY (source, model_id, metric, task_type)
);

CREATE INDEX IF NOT EXISTS idx_external_ranks_model_task
    ON control_plane.external_ranks(model_id, task_type);

CREATE INDEX IF NOT EXISTS idx_external_ranks_fetched
    ON control_plane.external_ranks(fetched_at DESC);
