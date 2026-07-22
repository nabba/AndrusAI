-- Physically separate governance boundary.
-- Apply only to GOVERNANCE_DATABASE_URL, never the durable-memory database.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS governance_boundary;
REVOKE ALL ON SCHEMA governance_boundary FROM PUBLIC;

DO $roles$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'governance_event_writer') THEN
        CREATE ROLE governance_event_writer NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'governance_reader') THEN
        CREATE ROLE governance_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'governance_auditor') THEN
        CREATE ROLE governance_auditor NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
    END IF;
END
$roles$;

CREATE TABLE IF NOT EXISTS governance_boundary.events (
    sequence_number bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id uuid NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    occurred_at timestamptz NOT NULL DEFAULT now(),
    event_type text NOT NULL CHECK (length(btrim(event_type)) > 0),
    actor_type text NOT NULL CHECK (actor_type IN ('operator', 'infrastructure', 'external_attestor')),
    actor_id text NOT NULL CHECK (length(btrim(actor_id)) > 0),
    subject text NOT NULL CHECK (length(btrim(subject)) > 0),
    decision text NOT NULL CHECK (length(btrim(decision)) > 0),
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(evidence) = 'object'),
    supersedes_event_id uuid REFERENCES governance_boundary.events(event_id),
    previous_hash bytea NOT NULL CHECK (octet_length(previous_hash) = 32),
    event_hash bytea NOT NULL UNIQUE CHECK (octet_length(event_hash) = 32)
);

CREATE TABLE IF NOT EXISTS governance_boundary.protected_artifact_attestations (
    sequence_number bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    attestation_id uuid NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    occurred_at timestamptz NOT NULL DEFAULT now(),
    artifact_path text NOT NULL,
    artifact_sha256 bytea NOT NULL CHECK (octet_length(artifact_sha256) = 32),
    criteria_version text NOT NULL,
    attestor_id text NOT NULL,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    previous_hash bytea NOT NULL CHECK (octet_length(previous_hash) = 32),
    attestation_hash bytea NOT NULL UNIQUE CHECK (octet_length(attestation_hash) = 32)
);

CREATE OR REPLACE FUNCTION governance_boundary.seal_event()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, governance_boundary
AS $function$
DECLARE
    prior bytea;
    canonical text;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('governance_boundary.events'));
    SELECT event_hash INTO prior
      FROM governance_boundary.events
     ORDER BY sequence_number DESC
     LIMIT 1;
    NEW.previous_hash := coalesce(prior, decode(repeat('00', 32), 'hex'));
    canonical := concat_ws(E'\x1f',
        NEW.event_id::text,
        NEW.occurred_at::text,
        NEW.event_type,
        NEW.actor_type,
        NEW.actor_id,
        NEW.subject,
        NEW.decision,
        NEW.evidence::text,
        coalesce(NEW.supersedes_event_id::text, ''),
        encode(NEW.previous_hash, 'hex')
    );
    NEW.event_hash := public.digest(convert_to(canonical, 'UTF8'), 'sha256');
    RETURN NEW;
END
$function$;

CREATE OR REPLACE FUNCTION governance_boundary.seal_attestation()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, governance_boundary
AS $function$
DECLARE
    prior bytea;
    canonical text;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('governance_boundary.protected_artifact_attestations'));
    SELECT attestation_hash INTO prior
      FROM governance_boundary.protected_artifact_attestations
     ORDER BY sequence_number DESC
     LIMIT 1;
    NEW.previous_hash := coalesce(prior, decode(repeat('00', 32), 'hex'));
    canonical := concat_ws(E'\x1f',
        NEW.attestation_id::text,
        NEW.occurred_at::text,
        NEW.artifact_path,
        encode(NEW.artifact_sha256, 'hex'),
        NEW.criteria_version,
        NEW.attestor_id,
        NEW.evidence::text,
        encode(NEW.previous_hash, 'hex')
    );
    NEW.attestation_hash := public.digest(convert_to(canonical, 'UTF8'), 'sha256');
    RETURN NEW;
END
$function$;

CREATE OR REPLACE FUNCTION governance_boundary.reject_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION 'governance history is append-only; append an amendment event';
END
$function$;

DROP TRIGGER IF EXISTS events_seal_before_insert ON governance_boundary.events;
CREATE TRIGGER events_seal_before_insert
    BEFORE INSERT ON governance_boundary.events
    FOR EACH ROW EXECUTE FUNCTION governance_boundary.seal_event();

DROP TRIGGER IF EXISTS events_reject_update_delete ON governance_boundary.events;
CREATE TRIGGER events_reject_update_delete
    BEFORE UPDATE OR DELETE ON governance_boundary.events
    FOR EACH ROW EXECUTE FUNCTION governance_boundary.reject_mutation();

DROP TRIGGER IF EXISTS attestations_seal_before_insert ON governance_boundary.protected_artifact_attestations;
CREATE TRIGGER attestations_seal_before_insert
    BEFORE INSERT ON governance_boundary.protected_artifact_attestations
    FOR EACH ROW EXECUTE FUNCTION governance_boundary.seal_attestation();

DROP TRIGGER IF EXISTS attestations_reject_update_delete ON governance_boundary.protected_artifact_attestations;
CREATE TRIGGER attestations_reject_update_delete
    BEFORE UPDATE OR DELETE ON governance_boundary.protected_artifact_attestations
    FOR EACH ROW EXECUTE FUNCTION governance_boundary.reject_mutation();

REVOKE ALL ON FUNCTION governance_boundary.seal_event() FROM PUBLIC;
REVOKE ALL ON FUNCTION governance_boundary.seal_attestation() FROM PUBLIC;
REVOKE ALL ON FUNCTION governance_boundary.reject_mutation() FROM PUBLIC;

GRANT USAGE ON SCHEMA governance_boundary TO governance_event_writer, governance_reader, governance_auditor;
GRANT INSERT ON governance_boundary.events,
                governance_boundary.protected_artifact_attestations
    TO governance_event_writer;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA governance_boundary TO governance_event_writer;
GRANT SELECT ON governance_boundary.events TO governance_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA governance_boundary TO governance_auditor;

REVOKE UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA governance_boundary FROM PUBLIC;
REVOKE UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA governance_boundary FROM governance_event_writer;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA governance_boundary FROM governance_reader;

ALTER DEFAULT PRIVILEGES IN SCHEMA governance_boundary REVOKE ALL ON TABLES FROM PUBLIC;
