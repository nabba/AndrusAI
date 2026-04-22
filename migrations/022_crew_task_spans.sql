-- ============================================================
-- MIGRATION 022: Crew task spans
--
-- One row per sub-event inside a crew task (agent execution,
-- tool call, LLM call). Captures the fine-grained execution
-- flow that `crew_tasks` (which tracks only crew boundaries)
-- can't show. Powers the dashboard's task-flow drawer.
--
-- Correlation strategy:
--   * ``task_id`` FKs into ``control_plane.crew_tasks`` — the
--     envelope for the whole crew run.
--   * ``parent_span_id`` reconstructs the event tree (agent →
--     tool → llm-call) within that task.
--   * ``crewai_event_id`` stores the CrewAI-emitted event UUID
--     so ``*_Finished`` events can find their span by lookup.
--
-- Retention: 7 days, swept by the idle scheduler's ``spans-retain``
-- job. Per-run overhead: ~5-20 INSERTs per typical crew run; the
-- DELETE…CASCADE drops spans automatically when the parent
-- crew_tasks row is deleted.
-- ============================================================

CREATE TABLE IF NOT EXISTS control_plane.crew_task_spans (
    id              BIGSERIAL PRIMARY KEY,
    task_id         TEXT NOT NULL
                    REFERENCES control_plane.crew_tasks(id) ON DELETE CASCADE,
    parent_span_id  BIGINT
                    REFERENCES control_plane.crew_task_spans(id) ON DELETE SET NULL,

    span_type       TEXT NOT NULL
                    CHECK (span_type IN ('agent','tool','llm_call')),
    name            TEXT NOT NULL,  -- agent role / tool name / model id

    -- CrewAI's internal event_id so `*_Completed` events can look
    -- up the matching span. Not a FK — CrewAI owns the space.
    crewai_event_id TEXT,

    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ,

    state           TEXT NOT NULL DEFAULT 'running'
                    CHECK (state IN ('running','completed','failed')),

    -- Free-form JSON: tool args preview, token usage, error type.
    -- Capped soft-budget at ~8 KB/row by the persistence layer.
    detail          JSONB NOT NULL DEFAULT '{}'::jsonb,
    error           TEXT
);

-- Hot path: "show me the timeline for task X, in order"
CREATE INDEX IF NOT EXISTS idx_crew_task_spans_task_started
    ON control_plane.crew_task_spans (task_id, started_at);

-- Tree build: walk from parent to children
CREATE INDEX IF NOT EXISTS idx_crew_task_spans_parent
    ON control_plane.crew_task_spans (parent_span_id)
    WHERE parent_span_id IS NOT NULL;

-- Retention sweep
CREATE INDEX IF NOT EXISTS idx_crew_task_spans_started_at
    ON control_plane.crew_task_spans (started_at);

-- CrewAI event_id lookup (for *_Completed-event → span_id lookup)
CREATE INDEX IF NOT EXISTS idx_crew_task_spans_crewai_event_id
    ON control_plane.crew_task_spans (crewai_event_id)
    WHERE crewai_event_id IS NOT NULL;

-- Analytics / heatmaps: "which tools run most often?"
CREATE INDEX IF NOT EXISTS idx_crew_task_spans_type_name
    ON control_plane.crew_task_spans (span_type, name, started_at DESC);
