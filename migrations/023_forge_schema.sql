-- 023_forge_schema.sql
-- Tool Forge: registry, audit log, invocations, compositions, settings.
-- Generated tools live here as data, never as Python imports into the gateway.

CREATE TABLE IF NOT EXISTS forge_tools (
    tool_id              TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    version              INT  NOT NULL DEFAULT 1,
    status               TEXT NOT NULL DEFAULT 'DRAFT',
    source_type          TEXT NOT NULL,
    description          TEXT,
    manifest             JSONB NOT NULL,
    source_code          TEXT,
    generator_metadata   JSONB NOT NULL DEFAULT '{}'::jsonb,
    security_eval        JSONB,
    audit_results        JSONB NOT NULL DEFAULT '[]'::jsonb,
    risk_score           NUMERIC(4,2),
    parent_tool_id       TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status_changed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    killed_at            TIMESTAMPTZ,
    killed_reason        TEXT,
    CONSTRAINT forge_status_check CHECK (status IN (
        'DRAFT','QUARANTINED','SHADOW','CANARY','ACTIVE','DEPRECATED','KILLED'
    )),
    CONSTRAINT forge_source_type_check CHECK (source_type IN (
        'declarative','python_sandbox'
    ))
);

CREATE INDEX IF NOT EXISTS idx_forge_tools_status ON forge_tools (status);
CREATE INDEX IF NOT EXISTS idx_forge_tools_created ON forge_tools (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_forge_tools_parent ON forge_tools (parent_tool_id);


CREATE TABLE IF NOT EXISTS forge_audit_log (
    id           BIGSERIAL PRIMARY KEY,
    tool_id      TEXT,
    event_type   TEXT NOT NULL,
    from_status  TEXT,
    to_status    TEXT,
    actor        TEXT NOT NULL,
    reason       TEXT,
    audit_data   JSONB NOT NULL DEFAULT '{}'::jsonb,
    prev_hash    TEXT,
    entry_hash   TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_forge_audit_tool ON forge_audit_log (tool_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_forge_audit_event ON forge_audit_log (event_type, created_at DESC);


CREATE TABLE IF NOT EXISTS forge_invocations (
    id                       BIGSERIAL PRIMARY KEY,
    tool_id                  TEXT NOT NULL,
    tool_version             INT  NOT NULL,
    caller_crew_id           TEXT,
    caller_agent             TEXT,
    request_id               TEXT,
    composition_id           TEXT,
    inputs_redacted          JSONB,
    output_hash              TEXT,
    output_size              INT,
    capabilities_declared    TEXT[],
    capabilities_used        TEXT[],
    capability_violations    TEXT[],
    duration_ms              INT,
    error                    TEXT,
    mode                     TEXT NOT NULL,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_forge_inv_tool ON forge_invocations (tool_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_forge_inv_mode ON forge_invocations (mode, created_at DESC);


CREATE TABLE IF NOT EXISTS forge_compositions (
    id                       BIGSERIAL PRIMARY KEY,
    composition_id           TEXT NOT NULL,
    tool_ids                 TEXT[] NOT NULL,
    aggregate_capabilities   TEXT[] NOT NULL,
    call_graph               JSONB,
    risk_score               NUMERIC(4,2) NOT NULL,
    verdict                  TEXT NOT NULL,
    judge_explanation        TEXT,
    approved_by              TEXT,
    approved_at              TIMESTAMPTZ,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT forge_comp_verdict_check CHECK (verdict IN (
        'allow','block','needs_human'
    ))
);

CREATE INDEX IF NOT EXISTS idx_forge_comp_id ON forge_compositions (composition_id);
CREATE INDEX IF NOT EXISTS idx_forge_comp_created ON forge_compositions (created_at DESC);


CREATE TABLE IF NOT EXISTS forge_settings (
    key          TEXT PRIMARY KEY,
    value        JSONB NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by   TEXT
);

-- Default runtime override: not set means env value wins.
-- Possible keys: forge_runtime_enabled, forge_runtime_dry_run, killed_capability_classes
