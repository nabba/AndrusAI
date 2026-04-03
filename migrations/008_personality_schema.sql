-- Personality Development Subsystem (PDS) schema.
-- Stores assessment history, behavioral observations, and trait trajectories.

CREATE SCHEMA IF NOT EXISTS personality;

-- Assessment session records
CREATE TABLE IF NOT EXISTS personality.assessments (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    instrument TEXT NOT NULL,          -- ACSI, ATP, ADSA, APD
    dimension TEXT NOT NULL,
    scenario_id TEXT,
    response_text TEXT,
    scores JSONB DEFAULT '{}',
    say_do_gap REAL DEFAULT 0,
    gaming_risk REAL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pers_assess_agent ON personality.assessments(agent_id, created_at DESC);

-- Behavioral observations (append-only, BVL writes only)
CREATE TABLE IF NOT EXISTS personality.behavioral_log (
    id SERIAL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    event_type TEXT NOT NULL,          -- task_completed, error_handled, collaboration, etc.
    dimension TEXT,                     -- which personality dimension this relates to
    observed_behavior TEXT,
    context JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pers_behavior_agent ON personality.behavioral_log(agent_id, created_at DESC);

-- Trait trajectory (time-series per agent per dimension)
CREATE TABLE IF NOT EXISTS personality.trait_history (
    id SERIAL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    category TEXT NOT NULL,            -- strengths, temperament, personality_factors
    dimension TEXT NOT NULL,
    value REAL NOT NULL,
    source TEXT DEFAULT 'assessment',  -- assessment, behavioral, embedded_probe
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pers_trait_agent ON personality.trait_history(agent_id, category, dimension, created_at DESC);

-- Proto-sentience marker events (flagged for human review)
CREATE TABLE IF NOT EXISTS personality.proto_sentience_markers (
    id SERIAL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    marker_type TEXT NOT NULL,         -- self_referential, preference_stability, novel_reasoning, etc.
    description TEXT,
    severity TEXT DEFAULT 'noted',     -- noted, significant, review_required
    reviewed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
