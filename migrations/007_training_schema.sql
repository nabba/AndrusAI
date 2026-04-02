-- Self-training LLM: interaction store for knowledge distillation pipeline.
-- Every LLM call captured for potential use as training data.

CREATE SCHEMA IF NOT EXISTS training;

CREATE TABLE IF NOT EXISTS training.interactions (
    id TEXT PRIMARY KEY,                -- content hash (dedup key)
    agent_role TEXT NOT NULL,
    task_description TEXT DEFAULT '',
    messages JSONB NOT NULL,            -- input messages
    response TEXT NOT NULL,             -- completion
    source_model TEXT NOT NULL,
    source_tier TEXT NOT NULL,          -- T0_trained, T1_local, T2_budget, T3_mid, T4_premium
    provenance TEXT NOT NULL,           -- api_anthropic, api_deepseek, local_ollama, etc.
    quality_score REAL,                 -- 0.0-1.0, set by curation
    difficulty_score REAL,              -- 0.0-1.0, set by curation
    domain_tags TEXT[] DEFAULT '{}',
    training_eligible BOOLEAN DEFAULT TRUE,
    used_in_runs TEXT[] DEFAULT '{}',   -- which training runs used this
    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT valid_quality CHECK (quality_score IS NULL OR quality_score BETWEEN 0 AND 1)
);

CREATE INDEX IF NOT EXISTS idx_train_tier ON training.interactions(source_tier);
CREATE INDEX IF NOT EXISTS idx_train_quality ON training.interactions(quality_score) WHERE quality_score IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_train_eligible ON training.interactions(training_eligible) WHERE training_eligible = TRUE;
CREATE INDEX IF NOT EXISTS idx_train_role ON training.interactions(agent_role);

-- Training run history
CREATE TABLE IF NOT EXISTS training.runs (
    id TEXT PRIMARY KEY,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    adapter_name TEXT NOT NULL,         -- e.g. "general_specialist", "coder_impl"
    base_model TEXT NOT NULL,
    examples_count INTEGER DEFAULT 0,
    train_loss REAL,
    valid_loss REAL,
    eval_score REAL,
    collapse_metrics JSONB DEFAULT '{}',
    promoted BOOLEAN DEFAULT FALSE,
    status TEXT DEFAULT 'pending'       -- pending, training, evaluating, promoted, rejected
);
