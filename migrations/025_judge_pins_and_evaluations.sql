-- ============================================================
-- MIGRATION 025: Cross-eval judge pins + evaluation telemetry
--
-- Two tables that surface the previously-invisible judge subsystem:
--
-- 1. ``judge_pins`` — manual override for the dynamic
--    ``_discover_judges`` rotation. The rotation picks the
--    highest-intelligence catalog entry per provider family;
--    a row here forces a specific model for that family
--    (e.g. "always use claude-opus-4.7 as the Anthropic-family
--    judge"). The rotation falls back to dynamic when no pin
--    exists.
--
-- 2. ``judge_evaluations`` — one row per multi-judge scoring
--    pass. Captures every judge's score plus the inter-rater
--    standard deviation so the dashboard can surface
--    disagreement and detect biased / drifting judges.
--    Also records whether each judge call hit its OpenRouter
--    fallback (set when the direct API was out of credit).
-- ============================================================

CREATE TABLE IF NOT EXISTS control_plane.judge_pins (
    provider_family TEXT PRIMARY KEY,         -- 'anthropic' / 'openai' / 'google' / ...
    model           TEXT NOT NULL,            -- catalog key
    pinned_by       TEXT NOT NULL DEFAULT 'user',
    reason          TEXT,
    pinned_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS control_plane.judge_evaluations (
    id                BIGSERIAL PRIMARY KEY,

    -- Optional ties — not every judge call originates from a crew run.
    task_id           TEXT,
    candidate_model   TEXT NOT NULL,

    -- The panel that voted. Same length as ``scores`` and
    -- ``used_fallback``.
    judges            TEXT[] NOT NULL,
    scores            NUMERIC[] NOT NULL,
    used_fallback     BOOLEAN[] NOT NULL DEFAULT '{}'::boolean[],

    -- Pre-computed aggregates (saves the dashboard from doing
    -- array-mean on every read). NULL when only one judge voted.
    mean_score        NUMERIC,
    std_dev           NUMERIC,

    -- Free-form context for the dashboard tooltip.
    rubric            TEXT,
    task_description  TEXT,

    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_judge_eval_created
    ON control_plane.judge_evaluations (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_judge_eval_candidate
    ON control_plane.judge_evaluations (candidate_model, created_at DESC);

-- Retention: 30 days swept by the idle scheduler.
