-- ============================================================
-- MIGRATION 018: Model Promotions
-- Global "this model is promoted" flag, separate from per-(role,cost_mode)
-- hand pins in control_plane.role_assignments.
--
-- Semantic layer cake:
--   1. Pool        — model in live CATALOG; resolver scores like any other.
--   2. Promoted    — row in this table; resolver filters candidates to
--                    the promoted subset whenever at least one promoted
--                    model fits the role's hard constraints.
--   3. Hand-pinned — row in role_assignments with source='manual' and
--                    priority ≥ 1000; resolver returns it directly, no
--                    scoring. Strongest override.
--
-- Promotions are GLOBAL (not per-role) — the idea is "prefer this
-- model whenever it fits"; narrower preferences go through hand-pins.
-- ============================================================

CREATE TABLE IF NOT EXISTS control_plane.model_promotions (
    model        TEXT PRIMARY KEY,                         -- catalog key, e.g. 'kimi-k2.6'
    promoted_by  TEXT NOT NULL DEFAULT 'system',           -- 'user:dashboard', 'governance:user', 'llm_discovery', 'manual'
    reason       TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_model_promotions_created
    ON control_plane.model_promotions(created_at DESC);
