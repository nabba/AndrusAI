-- ============================================================
-- MIGRATION 016: LLM Role Assignment Overlay
-- Runtime overlay on top of the static ROLE_DEFAULTS in
-- app/llm_catalog.py. Populated by:
--   - llm_discovery._add_to_runtime_catalog when a discovered
--     model Pareto-dominates the incumbent
--   - llm_discovery.consume_approved_promotions after a human
--     approves a governance_requests row of type 'model_promotion'
--
-- get_default_for_role consults this table first, falling through
-- to the static catalog defaults if no active overlay exists for
-- the (role, cost_mode) pair.
-- ============================================================

CREATE TABLE IF NOT EXISTS control_plane.role_assignments (
    role          TEXT NOT NULL,
    cost_mode     TEXT NOT NULL
                  CHECK (cost_mode IN ('budget','balanced','quality')),
    model         TEXT NOT NULL,    -- catalog key (e.g. 'deepseek-v3.2')
    priority      INT NOT NULL DEFAULT 100,  -- higher wins; ties broken by created_at DESC
    source        TEXT NOT NULL DEFAULT 'manual'
                  CHECK (source IN ('manual','auto_promotion','governance','rebenchmark')),
    reason        TEXT,
    assigned_by   TEXT NOT NULL DEFAULT 'system',
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    retired_at    TIMESTAMPTZ,
    PRIMARY KEY (role, cost_mode, model)
);

CREATE INDEX IF NOT EXISTS idx_role_assignments_lookup
    ON control_plane.role_assignments(role, cost_mode, active, priority DESC, created_at DESC);

-- Tracks which governance requests have been consumed by the
-- promotion-applier so a single approval doesn't fire twice.
ALTER TABLE control_plane.governance_requests
    ADD COLUMN IF NOT EXISTS consumed_at TIMESTAMPTZ;
