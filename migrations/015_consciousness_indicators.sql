-- 015_consciousness_indicators.sql
-- Butlin et al. (2025) consciousness indicator tables: GWT-2, GWT-3, HOT-3
-- Requires: pgvector extension (from 012_internal_states.sql)

-- ═══ GWT-2: Competitive Workspace ═══════════════════════════════════════════

CREATE TABLE IF NOT EXISTS workspace_items (
    item_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content TEXT NOT NULL,
    content_embedding vector(768),
    source_agent VARCHAR(50) NOT NULL,
    source_channel VARCHAR(50) NOT NULL,
    salience_score FLOAT NOT NULL,
    goal_relevance FLOAT DEFAULT 0.0,
    novelty_score FLOAT DEFAULT 0.0,
    agent_urgency FLOAT DEFAULT 0.0,
    surprise_signal FLOAT DEFAULT 0.0,
    decay_rate FLOAT DEFAULT 0.05,
    entered_workspace_at TIMESTAMPTZ DEFAULT NOW(),
    exited_workspace_at TIMESTAMPTZ,
    exit_reason VARCHAR(20),  -- displaced, decayed, consumed, broadcast_complete
    is_active BOOLEAN DEFAULT TRUE,
    cycle_number BIGINT DEFAULT 0,
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS workspace_transitions (
    transition_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    transition_type VARCHAR(20) NOT NULL,  -- admitted, displaced, rejected, decayed, novelty_floor
    item_id UUID REFERENCES workspace_items(item_id),
    displaced_item_id UUID REFERENCES workspace_items(item_id),
    salience_at_transition FLOAT,
    cycle_number BIGINT
);

CREATE INDEX IF NOT EXISTS idx_workspace_active ON workspace_items(is_active) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_workspace_salience ON workspace_items(salience_score DESC) WHERE is_active = TRUE;

-- ═══ GWT-3: Global Broadcast ════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS broadcast_events (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_item_id UUID REFERENCES workspace_items(item_id),
    broadcast_at TIMESTAMPTZ DEFAULT NOW(),
    broadcast_cycle BIGINT,
    receiving_agents TEXT[] NOT NULL,
    integration_score FLOAT DEFAULT 0.0,
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS broadcast_reactions (
    reaction_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id UUID REFERENCES broadcast_events(event_id),
    agent_id VARCHAR(50) NOT NULL,
    reaction_type VARCHAR(20) NOT NULL,  -- NOTED, RELEVANT, URGENT, ACTIONABLE
    relevance_score FLOAT,
    relevance_reason TEXT,
    proposed_action TEXT,
    reacted_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_broadcast_cycle ON broadcast_events(broadcast_cycle);
CREATE INDEX IF NOT EXISTS idx_reactions_event ON broadcast_reactions(event_id);

-- ═══ HOT-3: Belief Store ════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS beliefs (
    belief_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content TEXT NOT NULL,
    content_embedding vector(768),
    domain VARCHAR(50) NOT NULL,  -- task_strategy, user_model, self_model, world_model, agent_capability, environment
    confidence FLOAT NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    evidence_sources JSONB DEFAULT '[]'::jsonb,
    formed_at TIMESTAMPTZ DEFAULT NOW(),
    last_validated TIMESTAMPTZ DEFAULT NOW(),
    last_updated TIMESTAMPTZ DEFAULT NOW(),
    metacognitive_flags JSONB DEFAULT '[]'::jsonb,
    update_history JSONB DEFAULT '[]'::jsonb,
    belief_status VARCHAR(20) DEFAULT 'ACTIVE',
    superseded_by UUID REFERENCES beliefs(belief_id),
    CHECK (belief_status IN ('ACTIVE', 'SUSPENDED', 'RETRACTED', 'SUPERSEDED'))
);

CREATE TABLE IF NOT EXISTS metacognitive_updates (
    update_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_belief_id UUID REFERENCES beliefs(belief_id),
    trigger VARCHAR(30) NOT NULL,
    observation TEXT NOT NULL,
    action_taken VARCHAR(30) NOT NULL,
    old_confidence FLOAT,
    new_confidence FLOAT,
    reasoning TEXT,
    timestamp TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS action_selection_records (
    action_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    selected_action TEXT NOT NULL,
    beliefs_consulted UUID[],
    goal_context TEXT,
    alternatives_considered JSONB,
    selection_reasoning TEXT NOT NULL,
    outcome_assessed BOOLEAN DEFAULT FALSE,
    outcome_matched_prediction BOOLEAN,
    timestamp TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_beliefs_active ON beliefs(belief_status) WHERE belief_status = 'ACTIVE';
CREATE INDEX IF NOT EXISTS idx_beliefs_domain ON beliefs(domain);
CREATE INDEX IF NOT EXISTS idx_beliefs_confidence ON beliefs(confidence DESC);
CREATE INDEX IF NOT EXISTS idx_beliefs_embedding ON beliefs USING ivfflat (content_embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_beliefs_last_validated ON beliefs(last_validated ASC);
CREATE INDEX IF NOT EXISTS idx_meta_updates_belief ON metacognitive_updates(source_belief_id);
