-- 036_code_intel.sql — Verified Implementation Plan §5 closure (Gap 1, 2026-05-23)
--
-- Three tables matching the plan's spec for the code_intel subsystem.
-- The current production storage is JSONL at workspace/code_intel/index.jsonl
-- (operational since Phase 3); this migration adds the OPTIONAL Postgres
-- backend behind ``code_intel_postgres_enabled`` (default OFF) so operators
-- can flip when JSONL scale becomes a bottleneck (~10k+ files).
--
-- All three tables are idempotent (IF NOT EXISTS) and contain no data
-- on first apply. Schema follows the dataclasses in
-- ``app/code_intel/models.py``.
--
-- Rollback: ``DROP TABLE code_symbols, code_references, code_coverage_snapshot;``
-- with no business-impact loss (JSONL backend remains canonical).

BEGIN;

-- ── code_symbols ──────────────────────────────────────────────────
-- One row per definition site (function / class / method / async).
-- Indexed by (file_path, name, lineno) for the "where is X defined?"
-- query path; (parent, name) for class-scoped method lookups.

CREATE TABLE IF NOT EXISTS code_symbols (
    id           BIGSERIAL PRIMARY KEY,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL,
    file_path    TEXT NOT NULL,
    lineno       INT  NOT NULL,
    end_lineno   INT  NOT NULL,
    parent       TEXT DEFAULT '' NOT NULL,
    docstring    TEXT DEFAULT '' NOT NULL,
    language     TEXT DEFAULT 'python' NOT NULL,
    -- Free-form metadata bucket: tree-sitter node-kind, type hints
    -- harvested by pyright, etc. Optional.
    extra        JSONB DEFAULT '{}'::jsonb NOT NULL,
    indexed_at   TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    UNIQUE (file_path, name, lineno, parent)
);

CREATE INDEX IF NOT EXISTS idx_code_symbols_name
    ON code_symbols (name);
CREATE INDEX IF NOT EXISTS idx_code_symbols_file
    ON code_symbols (file_path);
CREATE INDEX IF NOT EXISTS idx_code_symbols_parent_name
    ON code_symbols (parent, name) WHERE parent != '';


-- ── code_references ───────────────────────────────────────────────
-- One row per usage site. Many-to-one with code_symbols logically
-- (we don't enforce FK because cross-language references are
-- best-effort name-matching, not semantic resolution).

CREATE TABLE IF NOT EXISTS code_references (
    id           BIGSERIAL PRIMARY KEY,
    name         TEXT NOT NULL,
    file_path    TEXT NOT NULL,
    lineno       INT  NOT NULL,
    col_offset   INT  DEFAULT 0 NOT NULL,
    in_function  TEXT DEFAULT '' NOT NULL,
    in_class     TEXT DEFAULT '' NOT NULL,
    language     TEXT DEFAULT 'python' NOT NULL,
    extra        JSONB DEFAULT '{}'::jsonb NOT NULL,
    indexed_at   TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_code_references_name
    ON code_references (name);
CREATE INDEX IF NOT EXISTS idx_code_references_file
    ON code_references (file_path);
CREATE INDEX IF NOT EXISTS idx_code_references_in_function
    ON code_references (in_function) WHERE in_function != '';


-- ── code_coverage_snapshot ────────────────────────────────────────
-- Per-file coverage statistics, one row per indexer run per file.
-- Populated by ``code_intel.coverage`` reading ``.coverage`` from the
-- pytest run (when present). Used by the ``finds-test-coverage``
-- capability.

CREATE TABLE IF NOT EXISTS code_coverage_snapshot (
    id             BIGSERIAL PRIMARY KEY,
    file_path      TEXT NOT NULL,
    line_count     INT  NOT NULL,
    covered_lines  INT  NOT NULL,
    missed_lines   INT  NOT NULL,
    coverage_pct   NUMERIC(5, 2) NOT NULL,
    snapshot_at    TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    UNIQUE (file_path, snapshot_at)
);

CREATE INDEX IF NOT EXISTS idx_code_coverage_file
    ON code_coverage_snapshot (file_path);
CREATE INDEX IF NOT EXISTS idx_code_coverage_snapshot_at
    ON code_coverage_snapshot (snapshot_at DESC);

COMMIT;
