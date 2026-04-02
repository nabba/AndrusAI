-- MAP-Elites quality-diversity grid storage
-- Supplements in-memory grid with persistent PostgreSQL backing.

CREATE SCHEMA IF NOT EXISTS evolution;

-- Strategy entries in the MAP-Elites grid
CREATE TABLE IF NOT EXISTS evolution.map_elites (
    id SERIAL PRIMARY KEY,
    strategy_id TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL,
    island_id INTEGER DEFAULT 0,
    bin_key TEXT NOT NULL,           -- discretized feature position e.g. "(3,7,2)"
    fitness_score REAL NOT NULL DEFAULT 0,
    feature_vector JSONB NOT NULL DEFAULT '{}',
    generation INTEGER DEFAULT 0,
    parent_id TEXT DEFAULT '',
    mutation_type TEXT DEFAULT '',
    prompt_hash TEXT DEFAULT '',     -- SHA-256 of prompt content (content stored on disk)
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_me_role_island ON evolution.map_elites(role, island_id);
CREATE INDEX IF NOT EXISTS idx_me_fitness ON evolution.map_elites(fitness_score DESC);
CREATE INDEX IF NOT EXISTS idx_me_bin ON evolution.map_elites(bin_key);

-- Artifact feedback history
CREATE TABLE IF NOT EXISTS evolution.artifacts (
    id SERIAL PRIMARY KEY,
    role TEXT NOT NULL,
    generation INTEGER DEFAULT 0,
    success BOOLEAN DEFAULT FALSE,
    score REAL DEFAULT 0,
    stage_reached TEXT DEFAULT '',
    stderr TEXT DEFAULT '',
    llm_feedback TEXT DEFAULT '',
    suggestion TEXT DEFAULT '',
    execution_time_ms REAL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_artifacts_role ON evolution.artifacts(role, created_at DESC);
