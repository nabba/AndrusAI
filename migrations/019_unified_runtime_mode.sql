-- ============================================================
-- MIGRATION 019: Unified runtime-mode column
--
-- Renames ``role_assignments.cost_mode`` to ``role_assignments.mode``
-- and expands the CHECK constraint from the 3-value cost-mode
-- vocabulary (budget/balanced/quality) to the 6-value unified
-- runtime-mode vocabulary:
--
--     free, budget, balanced, quality, insane, anthropic
--
-- Background: previously the system had TWO mode axes — a runtime
-- mode (free/hybrid/insane/anthropic, controlling which providers
-- were reachable) and a cost mode (budget/balanced/quality,
-- controlling per-role willingness-to-pay). At the extremes the two
-- collapsed (free implied budget; insane implied quality), and users
-- on the dashboard couldn't easily reason about why their commander
-- picked a specific model across 4×3 combinations. The unification
-- merges them into a single monotonic axis plus the Anthropic lock.
--
-- Data migration: existing values in {budget, balanced, quality}
-- already belong to the new vocabulary, so no row-level UPDATE is
-- required. Any legacy ``hybrid`` pins (shouldn't exist in this
-- column but defensive) are mapped to ``balanced``.
-- ============================================================

-- 1. Drop the old column-level CHECK (its name follows PostgreSQL's
--    auto-naming convention ``<table>_<column>_check``).
ALTER TABLE control_plane.role_assignments
    DROP CONSTRAINT IF EXISTS role_assignments_cost_mode_check;

-- 2. Defensive: rewrite any hypothetical 'hybrid' → 'balanced'.
UPDATE control_plane.role_assignments
   SET cost_mode = 'balanced'
 WHERE cost_mode = 'hybrid';

-- 3. Rename the column. Postgres rewires the primary-key constraint
--    and primary-key index automatically — no separate DROP needed.
ALTER TABLE control_plane.role_assignments
    RENAME COLUMN cost_mode TO mode;

-- 4. Install the new CHECK with the expanded vocabulary.
ALTER TABLE control_plane.role_assignments
    ADD CONSTRAINT role_assignments_mode_check
        CHECK (mode IN ('free','budget','balanced','quality','insane','anthropic'));

-- 5. Replace the hot-path lookup index with its renamed counterpart.
--    The old index referenced ``cost_mode`` in its definition string
--    and must be recreated to pick up the new column name.
DROP INDEX IF EXISTS control_plane.idx_role_assignments_lookup;
CREATE INDEX IF NOT EXISTS idx_role_assignments_lookup
    ON control_plane.role_assignments(role, mode, active, priority DESC, created_at DESC);
