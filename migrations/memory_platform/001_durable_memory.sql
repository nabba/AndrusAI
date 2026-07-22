-- Canonical durable-memory platform.
--
-- Applied only to the dedicated durable-memory PostgreSQL service by
-- scripts/apply_memory_platform_migrations.py.  It is intentionally outside
-- the historical startup-migration path for the existing Mem0 database.
--
-- All durable memory systems share operational tooling but retain separate
-- physical tables, HNSW indexes, FTS indexes, and grants.  Metadata filtering
-- is never the sole boundary between factual, fictional, autobiographical,
-- procedural, identity, and tenant memory.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS knowledge;
CREATE SCHEMA IF NOT EXISTS autobiographical;
CREATE SCHEMA IF NOT EXISTS identity_memory;
CREATE SCHEMA IF NOT EXISTS procedural;
CREATE SCHEMA IF NOT EXISTS creative;
CREATE SCHEMA IF NOT EXISTS tenant_memory;
CREATE SCHEMA IF NOT EXISTS memory_admin;

REVOKE ALL ON SCHEMA knowledge FROM PUBLIC;
REVOKE ALL ON SCHEMA autobiographical FROM PUBLIC;
REVOKE ALL ON SCHEMA identity_memory FROM PUBLIC;
REVOKE ALL ON SCHEMA procedural FROM PUBLIC;
REVOKE ALL ON SCHEMA creative FROM PUBLIC;
REVOKE ALL ON SCHEMA tenant_memory FROM PUBLIC;
REVOKE ALL ON SCHEMA memory_admin FROM PUBLIC;

-- Group roles have NOLOGIN.  Deployment creates separate LOGIN roles and
-- grants only the group memberships required by that service.
DO $roles$
DECLARE
    role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY[
        'memory_factual_reader',
        'memory_curated_reader',
        'memory_deep_recall_reader',
        'memory_affect_reader',
        'memory_belief_reader',
        'memory_private_identity_reader',
        'memory_procedural_reader',
        'memory_fiction_reader',
        'memory_creative_evaluative_reader',
        'memory_tenant_reader',
        'memory_knowledge_ingester',
        'memory_experiential_writer',
        'memory_identity_writer',
        'memory_procedural_writer',
        'memory_fiction_ingester',
        'memory_creative_evaluative_writer',
        'memory_tenant_writer',
        'memory_reconciler',
        'memory_retrieval_audit_writer',
        'memory_migration_coordinator',
        'memory_auditor'
    ] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format('CREATE ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT', role_name);
        END IF;
    END LOOP;
END
$roles$;

CREATE TABLE IF NOT EXISTS memory_admin.memory_item_template (
    memory_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    content text NOT NULL CHECK (length(btrim(content)) > 0),
    embedding vector(768),
    search_vector tsvector GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(content, ''))
    ) STORED,
    source_uri text NOT NULL CHECK (length(btrim(source_uri)) > 0),
    source_record_id text NOT NULL CHECK (length(btrim(source_record_id)) > 0),
    content_sha256 bytea NOT NULL CHECK (octet_length(content_sha256) = 32),
    epistemic_class text NOT NULL CHECK (
        epistemic_class IN (
            'factual', 'theoretical', 'subjective', 'episodic', 'narrative',
            'affective', 'belief', 'procedural', 'fictional', 'evaluative',
            'dialectical', 'tenant_context', 'operational'
        )
    ),
    provenance jsonb NOT NULL CHECK (jsonb_typeof(provenance) = 'object' AND provenance <> '{}'::jsonb),
    owner_agent_id text,
    tenant_id text,
    workspace_id text,
    event_time timestamptz,
    valid_from timestamptz,
    valid_to timestamptz,
    confidence real CHECK (confidence IS NULL OR confidence BETWEEN 0.0 AND 1.0),
    salience real CHECK (salience IS NULL OR salience BETWEEN 0.0 AND 1.0),
    significance real CHECK (significance IS NULL OR significance BETWEEN 0.0 AND 1.0),
    valence real CHECK (valence IS NULL OR valence BETWEEN -1.0 AND 1.0),
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'superseded', 'retracted', 'archived')),
    schema_version integer NOT NULL DEFAULT 1 CHECK (schema_version > 0),
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(attributes) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_uri, source_record_id, content_sha256),
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from)
);

-- LIKE creates genuinely separate tables.  Each receives its own HNSW and
-- GIN indexes below rather than relying on a memory_space metadata predicate.
CREATE TABLE IF NOT EXISTS knowledge.episteme_chunks
    (LIKE memory_admin.memory_item_template INCLUDING ALL);
CREATE TABLE IF NOT EXISTS knowledge.philosophy_claims
    (LIKE memory_admin.memory_item_template INCLUDING ALL);
CREATE TABLE IF NOT EXISTS knowledge.philosophy_counterclaims
    (LIKE memory_admin.memory_item_template INCLUDING ALL);
CREATE TABLE IF NOT EXISTS knowledge.enterprise_chunks
    (LIKE memory_admin.memory_item_template INCLUDING ALL);

CREATE TABLE IF NOT EXISTS autobiographical.experiential_entries
    (LIKE memory_admin.memory_item_template INCLUDING ALL);
CREATE TABLE IF NOT EXISTS autobiographical.episodic_full
    (LIKE memory_admin.memory_item_template INCLUDING ALL);
CREATE TABLE IF NOT EXISTS autobiographical.episodic_curated
    (LIKE memory_admin.memory_item_template INCLUDING ALL);
CREATE TABLE IF NOT EXISTS autobiographical.narrative_chapters
    (LIKE memory_admin.memory_item_template INCLUDING ALL);
CREATE TABLE IF NOT EXISTS autobiographical.affect_events
    (LIKE memory_admin.memory_item_template INCLUDING ALL);
CREATE TABLE IF NOT EXISTS autobiographical.self_reports
    (LIKE memory_admin.memory_item_template INCLUDING ALL);

CREATE TABLE IF NOT EXISTS identity_memory.beliefs
    (LIKE memory_admin.memory_item_template INCLUDING ALL);
CREATE TABLE IF NOT EXISTS identity_memory.predictions
    (LIKE memory_admin.memory_item_template INCLUDING ALL);
CREATE TABLE IF NOT EXISTS identity_memory.prediction_errors
    (LIKE memory_admin.memory_item_template INCLUDING ALL);
CREATE TABLE IF NOT EXISTS identity_memory.self_knowledge
    (LIKE memory_admin.memory_item_template INCLUDING ALL);
CREATE TABLE IF NOT EXISTS identity_memory.world_model
    (LIKE memory_admin.memory_item_template INCLUDING ALL);
CREATE TABLE IF NOT EXISTS identity_memory.ecology
    (LIKE memory_admin.memory_item_template INCLUDING ALL);

CREATE TABLE IF NOT EXISTS procedural.skills
    (LIKE memory_admin.memory_item_template INCLUDING ALL);
CREATE TABLE IF NOT EXISTS procedural.trajectory_lessons
    (LIKE memory_admin.memory_item_template INCLUDING ALL);
CREATE TABLE IF NOT EXISTS procedural.transfer_insights
    (LIKE memory_admin.memory_item_template INCLUDING ALL);
CREATE TABLE IF NOT EXISTS procedural.learned_policies
    (LIKE memory_admin.memory_item_template INCLUDING ALL);
CREATE TABLE IF NOT EXISTS procedural.evolution_lessons
    (LIKE memory_admin.memory_item_template INCLUDING ALL);
CREATE TABLE IF NOT EXISTS procedural.learning_gaps
    (LIKE memory_admin.memory_item_template INCLUDING ALL);
CREATE TABLE IF NOT EXISTS procedural.tool_knowledge
    (LIKE memory_admin.memory_item_template INCLUDING ALL);

CREATE TABLE IF NOT EXISTS creative.fiction_chunks
    (LIKE memory_admin.memory_item_template INCLUDING ALL);
CREATE TABLE IF NOT EXISTS creative.aesthetic_patterns
    (LIKE memory_admin.memory_item_template INCLUDING ALL);
CREATE TABLE IF NOT EXISTS creative.unresolved_tensions
    (LIKE memory_admin.memory_item_template INCLUDING ALL);
CREATE TABLE IF NOT EXISTS creative.ideas
    (LIKE memory_admin.memory_item_template INCLUDING ALL);

CREATE TABLE IF NOT EXISTS tenant_memory.project_documents
    (LIKE memory_admin.memory_item_template INCLUDING ALL);
CREATE TABLE IF NOT EXISTS tenant_memory.project_experiences
    (LIKE memory_admin.memory_item_template INCLUDING ALL);
CREATE TABLE IF NOT EXISTS tenant_memory.project_lessons
    (LIKE memory_admin.memory_item_template INCLUDING ALL);

CREATE TABLE IF NOT EXISTS memory_admin.memory_spaces (
    space_key text PRIMARY KEY,
    qualified_table text NOT NULL UNIQUE,
    epistemic_class text NOT NULL,
    tenant_scoped boolean NOT NULL DEFAULT false,
    spontaneous_eligible boolean NOT NULL DEFAULT false,
    description text NOT NULL DEFAULT '',
    registered_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO memory_admin.memory_spaces (
    space_key, qualified_table, epistemic_class, tenant_scoped,
    spontaneous_eligible, description
) VALUES
    ('knowledge.episteme', 'knowledge.episteme_chunks', 'factual', false, false, 'Research evidence and methods'),
    ('knowledge.philosophy', 'knowledge.philosophy_claims', 'theoretical', false, false, 'Philosophical claims'),
    ('knowledge.philosophy_counterclaims', 'knowledge.philosophy_counterclaims', 'dialectical', false, false, 'Philosophical counterclaims'),
    ('knowledge.enterprise', 'knowledge.enterprise_chunks', 'factual', false, false, 'Enterprise knowledge'),
    ('autobiographical.experiential', 'autobiographical.experiential_entries', 'subjective', false, false, 'Subjective reflections'),
    ('autobiographical.episodic_full', 'autobiographical.episodic_full', 'episodic', false, false, 'Full episode stream'),
    ('autobiographical.episodic_curated', 'autobiographical.episodic_curated', 'episodic', false, true, 'Curated episodes'),
    ('autobiographical.narrative', 'autobiographical.narrative_chapters', 'narrative', false, false, 'Narrative continuity'),
    ('autobiographical.affect', 'autobiographical.affect_events', 'affective', false, false, 'Affect trace'),
    ('autobiographical.self_reports', 'autobiographical.self_reports', 'subjective', false, false, 'Explicit self reports'),
    ('identity.beliefs', 'identity_memory.beliefs', 'belief', false, false, 'Revisable beliefs'),
    ('identity.predictions', 'identity_memory.predictions', 'belief', false, false, 'Predictions'),
    ('identity.prediction_errors', 'identity_memory.prediction_errors', 'belief', false, false, 'Prediction errors'),
    ('identity.self_knowledge', 'identity_memory.self_knowledge', 'belief', false, false, 'System self knowledge'),
    ('identity.world_model', 'identity_memory.world_model', 'belief', false, false, 'Revisable world model'),
    ('identity.ecology', 'identity_memory.ecology', 'belief', false, false, 'Social and ecological model'),
    ('procedural.skills', 'procedural.skills', 'procedural', false, false, 'Learned skills'),
    ('procedural.trajectory_lessons', 'procedural.trajectory_lessons', 'procedural', false, false, 'Trajectory lessons'),
    ('procedural.transfer_insights', 'procedural.transfer_insights', 'procedural', false, false, 'Transfer insights'),
    ('procedural.learned_policies', 'procedural.learned_policies', 'procedural', false, false, 'Learned policies, not governance'),
    ('procedural.evolution_lessons', 'procedural.evolution_lessons', 'procedural', false, false, 'Improvement experiment lessons'),
    ('procedural.learning_gaps', 'procedural.learning_gaps', 'procedural', false, false, 'Capability gaps'),
    ('procedural.tools', 'procedural.tool_knowledge', 'procedural', false, false, 'Tool retrieval projection'),
    ('creative.fiction', 'creative.fiction_chunks', 'fictional', false, false, 'Imaginary source material'),
    ('creative.aesthetics', 'creative.aesthetic_patterns', 'evaluative', false, false, 'Aesthetic judgements'),
    ('creative.tensions', 'creative.unresolved_tensions', 'dialectical', false, false, 'Unresolved contradictions'),
    ('creative.ideas', 'creative.ideas', 'evaluative', false, false, 'Ideas and lineage'),
    ('tenant.documents', 'tenant_memory.project_documents', 'tenant_context', true, false, 'Tenant documents'),
    ('tenant.experiences', 'tenant_memory.project_experiences', 'tenant_context', true, false, 'Tenant experiences'),
    ('tenant.lessons', 'tenant_memory.project_lessons', 'tenant_context', true, false, 'Tenant lessons')
ON CONFLICT (space_key) DO UPDATE SET
    qualified_table = EXCLUDED.qualified_table,
    epistemic_class = EXCLUDED.epistemic_class,
    tenant_scoped = EXCLUDED.tenant_scoped,
    spontaneous_eligible = EXCLUDED.spontaneous_eligible,
    description = EXCLUDED.description;

-- Per-table indexes.  The table list comes from the immutable registry seeded
-- above; format('%I.%I') safely quotes each identifier.
DO $indexes$
DECLARE
    item record;
    schema_name text;
    table_name text;
    index_prefix text;
BEGIN
    FOR item IN SELECT qualified_table FROM memory_admin.memory_spaces LOOP
        schema_name := split_part(item.qualified_table, '.', 1);
        table_name := split_part(item.qualified_table, '.', 2);
        index_prefix := left(schema_name || '_' || table_name, 48);
        EXECUTE format(
            'CREATE INDEX IF NOT EXISTS %I ON %I.%I USING hnsw (embedding vector_cosine_ops) WHERE embedding IS NOT NULL',
            index_prefix || '_emb_hnsw', schema_name, table_name
        );
        EXECUTE format(
            'CREATE INDEX IF NOT EXISTS %I ON %I.%I USING gin (search_vector)',
            index_prefix || '_fts_gin', schema_name, table_name
        );
        EXECUTE format(
            'CREATE INDEX IF NOT EXISTS %I ON %I.%I (event_time DESC NULLS LAST)',
            index_prefix || '_event_time', schema_name, table_name
        );
    END LOOP;
END
$indexes$;

CREATE TABLE IF NOT EXISTS memory_admin.memory_edges (
    edge_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    from_space text NOT NULL REFERENCES memory_admin.memory_spaces(space_key),
    from_memory_id uuid NOT NULL,
    to_space text NOT NULL REFERENCES memory_admin.memory_spaces(space_key),
    to_memory_id uuid NOT NULL,
    edge_type text NOT NULL CHECK (
        edge_type IN (
            'supports', 'contradicts', 'derived_from', 'promoted_from',
            'continues', 'revises', 'associated_with', 'inspired_by'
        )
    ),
    confidence real CHECK (confidence IS NULL OR confidence BETWEEN 0.0 AND 1.0),
    provenance jsonb NOT NULL CHECK (jsonb_typeof(provenance) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (from_space, from_memory_id, to_space, to_memory_id, edge_type)
);
CREATE INDEX IF NOT EXISTS memory_edges_from_idx
    ON memory_admin.memory_edges (from_space, from_memory_id, edge_type);
CREATE INDEX IF NOT EXISTS memory_edges_to_idx
    ON memory_admin.memory_edges (to_space, to_memory_id, edge_type);

CREATE TABLE IF NOT EXISTS memory_admin.reconciliation_outbox (
    event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    memory_space text NOT NULL REFERENCES memory_admin.memory_spaces(space_key),
    memory_id uuid NOT NULL,
    operation text NOT NULL CHECK (operation IN ('upsert', 'retract', 'archive')),
    content_sha256 bytea NOT NULL CHECK (octet_length(content_sha256) = 32),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    available_at timestamptz NOT NULL DEFAULT now(),
    claimed_at timestamptz,
    completed_at timestamptz,
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    last_error text,
    UNIQUE (memory_space, memory_id, content_sha256, operation)
);
CREATE INDEX IF NOT EXISTS reconciliation_outbox_pending_idx
    ON memory_admin.reconciliation_outbox (available_at, event_id)
    WHERE completed_at IS NULL;

CREATE TABLE IF NOT EXISTS memory_admin.migration_state (
    memory_space text PRIMARY KEY REFERENCES memory_admin.memory_spaces(space_key),
    phase text NOT NULL CHECK (
        phase IN (
            'discovered', 'schema_ready', 'backfilled', 'dual_write',
            'shadow_read', 'ready', 'cutover', 'soak', 'retired', 'aborted'
        )
    ),
    source_checkpoint text,
    expected_records bigint CHECK (expected_records IS NULL OR expected_records >= 0),
    migrated_records bigint NOT NULL DEFAULT 0 CHECK (migrated_records >= 0),
    outbox_lag_seconds double precision,
    shadow_queries bigint NOT NULL DEFAULT 0 CHECK (shadow_queries >= 0),
    mean_ndcg_at_10 double precision,
    provenance_completeness double precision,
    permission_violations bigint NOT NULL DEFAULT 0 CHECK (permission_violations >= 0),
    operator_approval_id text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    details jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS memory_admin.retrieval_audit (
    audit_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    actor_role text NOT NULL,
    actor_id text NOT NULL,
    tenant_id text,
    memory_space text REFERENCES memory_admin.memory_spaces(space_key),
    bridge_name text,
    route text NOT NULL CHECK (route IN ('legacy', 'target', 'shadow')),
    result_count integer NOT NULL CHECK (result_count >= 0),
    latency_ms double precision CHECK (latency_ms IS NULL OR latency_ms >= 0),
    denied boolean NOT NULL DEFAULT false,
    denial_reason text,
    query_sha256 bytea NOT NULL CHECK (octet_length(query_sha256) = 32)
);
CREATE INDEX IF NOT EXISTS retrieval_audit_space_time_idx
    ON memory_admin.retrieval_audit (memory_space, occurred_at DESC);

CREATE OR REPLACE FUNCTION memory_admin.enqueue_memory_change()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, memory_admin
AS $function$
DECLARE
    operation_name text;
BEGIN
    IF current_setting('memory_platform.suppress_outbox', true) = 'on' THEN
        RETURN NEW;
    END IF;
    operation_name := CASE NEW.status
        WHEN 'retracted' THEN 'retract'
        WHEN 'archived' THEN 'archive'
        ELSE 'upsert'
    END;
    INSERT INTO memory_admin.reconciliation_outbox (
        memory_space, memory_id, operation, content_sha256, payload
    ) VALUES (
        TG_ARGV[0], NEW.memory_id, operation_name, NEW.content_sha256,
        jsonb_build_object(
            'source_uri', NEW.source_uri,
            'source_record_id', NEW.source_record_id,
            'schema_version', NEW.schema_version,
            'epistemic_class', NEW.epistemic_class
        )
    ) ON CONFLICT DO NOTHING;
    RETURN NEW;
END
$function$;

DO $triggers$
DECLARE
    item record;
    schema_name text;
    table_name text;
    trigger_name text;
BEGIN
    FOR item IN SELECT space_key, qualified_table FROM memory_admin.memory_spaces LOOP
        schema_name := split_part(item.qualified_table, '.', 1);
        table_name := split_part(item.qualified_table, '.', 2);
        trigger_name := left('memory_outbox_' || replace(item.space_key, '.', '_'), 63);
        IF NOT EXISTS (
            SELECT 1
              FROM pg_trigger
             WHERE tgname = trigger_name
               AND tgrelid = to_regclass(item.qualified_table)
        ) THEN
            EXECUTE format(
                'CREATE TRIGGER %I AFTER INSERT OR UPDATE ON %I.%I FOR EACH ROW EXECUTE FUNCTION memory_admin.enqueue_memory_change(%L)',
                trigger_name, schema_name, table_name, item.space_key
            );
        END IF;
    END LOOP;
END
$triggers$;

-- Tenant isolation is enforced in PostgreSQL in addition to the broker.
ALTER TABLE tenant_memory.project_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_memory.project_documents FORCE ROW LEVEL SECURITY;
ALTER TABLE tenant_memory.project_experiences ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_memory.project_experiences FORCE ROW LEVEL SECURITY;
ALTER TABLE tenant_memory.project_lessons ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_memory.project_lessons FORCE ROW LEVEL SECURITY;

DO $policies$
DECLARE
    table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['project_documents', 'project_experiences', 'project_lessons'] LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
             WHERE schemaname = 'tenant_memory'
               AND tablename = table_name
               AND policyname = 'tenant_memory_isolation'
        ) THEN
            EXECUTE format(
                'CREATE POLICY tenant_memory_isolation ON tenant_memory.%I USING (tenant_id = nullif(current_setting(''app.tenant_id'', true), '''')) WITH CHECK (tenant_id = nullif(current_setting(''app.tenant_id'', true), ''''))',
                table_name
            );
        END IF;
    END LOOP;
END
$policies$;

-- Schema visibility.
GRANT USAGE ON SCHEMA knowledge TO memory_factual_reader, memory_knowledge_ingester, memory_auditor;
GRANT USAGE ON SCHEMA autobiographical TO memory_curated_reader, memory_deep_recall_reader, memory_affect_reader, memory_experiential_writer, memory_auditor;
GRANT USAGE ON SCHEMA identity_memory TO memory_belief_reader, memory_private_identity_reader, memory_identity_writer, memory_auditor;
GRANT USAGE ON SCHEMA procedural TO memory_procedural_reader, memory_procedural_writer, memory_auditor;
GRANT USAGE ON SCHEMA creative TO memory_fiction_reader, memory_creative_evaluative_reader, memory_fiction_ingester, memory_creative_evaluative_writer, memory_auditor;
GRANT USAGE ON SCHEMA tenant_memory TO memory_tenant_reader, memory_tenant_writer, memory_auditor;
GRANT USAGE ON SCHEMA memory_admin TO memory_reconciler, memory_retrieval_audit_writer, memory_migration_coordinator, memory_auditor;

-- Read boundaries.
GRANT SELECT ON ALL TABLES IN SCHEMA knowledge TO memory_factual_reader;
GRANT SELECT ON autobiographical.experiential_entries,
                autobiographical.episodic_curated,
                autobiographical.narrative_chapters,
                autobiographical.self_reports
    TO memory_curated_reader;
GRANT SELECT ON autobiographical.episodic_full,
                autobiographical.episodic_curated,
                autobiographical.narrative_chapters,
                autobiographical.affect_events
    TO memory_deep_recall_reader;
GRANT SELECT ON autobiographical.affect_events TO memory_affect_reader;
GRANT SELECT ON identity_memory.beliefs, identity_memory.world_model TO memory_belief_reader;
GRANT SELECT ON identity_memory.predictions,
                identity_memory.prediction_errors,
                identity_memory.self_knowledge,
                identity_memory.ecology
    TO memory_private_identity_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA procedural TO memory_procedural_reader;
GRANT SELECT ON creative.fiction_chunks TO memory_fiction_reader;
GRANT SELECT ON creative.aesthetic_patterns,
                creative.unresolved_tensions,
                creative.ideas
    TO memory_creative_evaluative_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA tenant_memory TO memory_tenant_reader;

-- Write boundaries.  Evaluation and governance tables do not exist here.
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA knowledge TO memory_knowledge_ingester;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA autobiographical TO memory_experiential_writer;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA identity_memory TO memory_identity_writer;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA procedural TO memory_procedural_writer;
GRANT SELECT, INSERT, UPDATE ON creative.fiction_chunks TO memory_fiction_ingester;
GRANT SELECT, INSERT, UPDATE ON creative.aesthetic_patterns,
                                creative.unresolved_tensions,
                                creative.ideas
    TO memory_creative_evaluative_writer;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA tenant_memory TO memory_tenant_writer;

GRANT SELECT, INSERT, UPDATE ON memory_admin.reconciliation_outbox TO memory_reconciler;
GRANT USAGE, SELECT ON SEQUENCE memory_admin.reconciliation_outbox_event_id_seq TO memory_reconciler;
GRANT SELECT ON memory_admin.memory_spaces, memory_admin.migration_state TO memory_reconciler;
GRANT INSERT ON memory_admin.retrieval_audit TO memory_retrieval_audit_writer;
GRANT USAGE, SELECT ON SEQUENCE memory_admin.retrieval_audit_audit_id_seq TO memory_retrieval_audit_writer;
GRANT SELECT ON memory_admin.memory_spaces TO memory_retrieval_audit_writer;
GRANT SELECT, INSERT, UPDATE ON memory_admin.migration_state TO memory_migration_coordinator;
GRANT SELECT ON memory_admin.memory_spaces TO memory_migration_coordinator;

GRANT SELECT ON ALL TABLES IN SCHEMA knowledge TO memory_auditor;
GRANT SELECT ON ALL TABLES IN SCHEMA autobiographical TO memory_auditor;
GRANT SELECT ON ALL TABLES IN SCHEMA identity_memory TO memory_auditor;
GRANT SELECT ON ALL TABLES IN SCHEMA procedural TO memory_auditor;
GRANT SELECT ON ALL TABLES IN SCHEMA creative TO memory_auditor;
GRANT SELECT ON ALL TABLES IN SCHEMA tenant_memory TO memory_auditor;
GRANT SELECT ON ALL TABLES IN SCHEMA memory_admin TO memory_auditor;

-- Future objects inherit the same deny-by-default posture.  Specific grants
-- must be added in a reviewed migration rather than being granted wholesale.
ALTER DEFAULT PRIVILEGES IN SCHEMA knowledge REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA autobiographical REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA identity_memory REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA procedural REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA creative REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA tenant_memory REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA memory_admin REVOKE ALL ON TABLES FROM PUBLIC;
