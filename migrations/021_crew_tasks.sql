-- Move crew task tracking off Firestore and into the Control Plane.
--
-- Background: until this migration the `tasks` and `crews` Firestore
-- collections backed the dashboard's Crew Activity view.  Reads from
-- the free-tier project were hitting the 50k-per-day quota, surfacing
-- as a "429 Quota exceeded" banner by evening.  Control-plane-native
-- storage gives us indexed queries, per-project filtering that matches
-- the rest of the dashboard, and no external quota.

CREATE TABLE IF NOT EXISTS control_plane.crew_tasks (
    id                 TEXT        PRIMARY KEY,               -- uuid.hex (matches legacy ids)
    crew               TEXT        NOT NULL,
    project_id         UUID        REFERENCES control_plane.projects(id) ON DELETE SET NULL,
    state              TEXT        NOT NULL DEFAULT 'running',  -- running | completed | failed
    summary            TEXT,
    result_preview     TEXT,
    error              TEXT,
    model              TEXT,
    tokens_used        BIGINT      NOT NULL DEFAULT 0,
    cost_usd           NUMERIC(12,6) NOT NULL DEFAULT 0,
    parent_task_id     TEXT,
    is_sub_agent       BOOLEAN     NOT NULL DEFAULT FALSE,
    delegated_from     TEXT,
    delegated_to       TEXT,
    delegation_reason  TEXT,
    delegation_ts      TIMESTAMPTZ,
    sub_agent_progress TEXT,
    heal_detail        TEXT,
    started_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at       TIMESTAMPTZ,
    eta                TIMESTAMPTZ,
    last_updated       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_crew_tasks_started_at
    ON control_plane.crew_tasks (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_crew_tasks_project_started
    ON control_plane.crew_tasks (project_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_crew_tasks_state
    ON control_plane.crew_tasks (state, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_crew_tasks_crew
    ON control_plane.crew_tasks (crew, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_crew_tasks_parent
    ON control_plane.crew_tasks (parent_task_id)
    WHERE parent_task_id IS NOT NULL;

-- No dedicated `crews` table: the dashboard derives per-crew status from
-- the most recent running task plus the canonical CREW_REGISTRY on the
-- frontend, so we never have a "missing crew" row to keep in sync.
