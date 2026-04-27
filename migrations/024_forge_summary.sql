-- 024_forge_summary.sql
-- Plain-language tool summary, generated at registration. Independent of the
-- LLM judge so a tool always has a readable description even when the
-- semantic auditor is offline.

ALTER TABLE forge_tools
    ADD COLUMN IF NOT EXISTS summary TEXT,
    ADD COLUMN IF NOT EXISTS summary_source TEXT;
-- summary_source values: 'llm', 'deterministic', or NULL if not yet generated.
