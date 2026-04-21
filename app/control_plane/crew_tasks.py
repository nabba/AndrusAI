"""Crew task lifecycle persistence — Control Plane replacement for the
legacy Firestore `tasks` / `crews` collections.

Same shape the frontend already consumes, so `/api/cp/tasks` just
switches its source here and the React dashboard keeps working.

All writes are fire-and-forget friendly — failures log at DEBUG so the
crew lifecycle never breaks because telemetry is unhappy.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.control_plane.db import execute, execute_one

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def start_task(
    *,
    task_id: str,
    crew: str,
    summary: str,
    project_id: str | None,
    parent_task_id: str | None = None,
    model: str | None = None,
    eta_seconds: int | None = None,
) -> None:
    """Insert a new `running` task row. Safe to call repeatedly — the
    PRIMARY KEY conflict is upserted so retries don't double-write."""
    eta = _utcnow() + timedelta(seconds=eta_seconds) if eta_seconds else None
    try:
        execute(
            """INSERT INTO control_plane.crew_tasks
                 (id, crew, project_id, state, summary, model,
                  parent_task_id, is_sub_agent, eta)
               VALUES (%s, %s, %s, 'running', %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO UPDATE
                 SET summary = EXCLUDED.summary,
                     model   = EXCLUDED.model,
                     eta     = EXCLUDED.eta,
                     last_updated = NOW()""",
            (
                task_id,
                crew,
                project_id,
                summary[:4000] if summary else "",
                (model or "")[:200],
                parent_task_id,
                parent_task_id is not None,
                eta,
            ),
        )
    except Exception as exc:
        logger.debug("crew_tasks.start_task failed: %s", exc)


def complete_task(
    *,
    task_id: str,
    result_preview: str = "",
    tokens_used: int = 0,
    model: str = "",
    cost_usd: float = 0.0,
) -> None:
    """Mark a task as completed, filling cost/model only when provided."""
    try:
        execute(
            """UPDATE control_plane.crew_tasks
                  SET state          = 'completed',
                      completed_at   = NOW(),
                      result_preview = %s,
                      tokens_used    = CASE WHEN %s > 0 THEN %s ELSE tokens_used END,
                      cost_usd       = CASE WHEN %s > 0 THEN %s ELSE cost_usd END,
                      model          = COALESCE(NULLIF(%s, ''), model),
                      last_updated   = NOW()
                WHERE id = %s""",
            (
                (result_preview or "")[:4000],
                tokens_used, tokens_used,
                cost_usd, cost_usd,
                (model or "")[:200],
                task_id,
            ),
        )
    except Exception as exc:
        logger.debug("crew_tasks.complete_task failed: %s", exc)


def fail_task(*, task_id: str, error: str = "") -> None:
    try:
        execute(
            """UPDATE control_plane.crew_tasks
                  SET state        = 'failed',
                      completed_at = NOW(),
                      error        = %s,
                      last_updated = NOW()
                WHERE id = %s""",
            ((error or "")[:300], task_id),
        )
    except Exception as exc:
        logger.debug("crew_tasks.fail_task failed: %s", exc)


def update_eta(*, task_id: str, eta_seconds: int) -> None:
    try:
        eta = _utcnow() + timedelta(seconds=eta_seconds)
        execute(
            """UPDATE control_plane.crew_tasks
                  SET eta = %s, last_updated = NOW()
                WHERE id = %s""",
            (eta, task_id),
        )
    except Exception as exc:
        logger.debug("crew_tasks.update_eta failed: %s", exc)


def mark_delegated(
    *, task_id: str, from_crew: str, to_crew: str, reason: str = ""
) -> None:
    try:
        execute(
            """UPDATE control_plane.crew_tasks
                  SET delegated_from    = %s,
                      delegated_to      = %s,
                      delegation_reason = %s,
                      delegation_ts     = NOW(),
                      last_updated      = NOW()
                WHERE id = %s""",
            (from_crew, to_crew, (reason or "")[:200], task_id),
        )
    except Exception as exc:
        logger.debug("crew_tasks.mark_delegated failed: %s", exc)


def update_sub_agent_progress(
    *, parent_task_id: str, completed: int, total: int
) -> None:
    try:
        execute(
            """UPDATE control_plane.crew_tasks
                  SET sub_agent_progress = %s, last_updated = NOW()
                WHERE id = %s""",
            (f"{completed}/{total}", parent_task_id),
        )
    except Exception as exc:
        logger.debug("crew_tasks.update_sub_agent_progress failed: %s", exc)


def mark_healed(*, task_id: str, heal_detail: str) -> None:
    try:
        execute(
            """UPDATE control_plane.crew_tasks
                  SET heal_detail = %s, last_updated = NOW()
                WHERE id = %s""",
            ((heal_detail or "")[:300], task_id),
        )
    except Exception as exc:
        logger.debug("crew_tasks.mark_healed failed: %s", exc)


def cleanup_zombies(max_age_hours: int = 6) -> int:
    """On startup: flip running tasks to failed if they're older than
    `max_age_hours`. Returns the count cleaned."""
    try:
        rows = execute(
            """UPDATE control_plane.crew_tasks
                  SET state        = 'failed',
                      error        = 'Task was running when system restarted. Marked as failed.',
                      completed_at = NOW(),
                      last_updated = NOW()
                WHERE state = 'running'
                  AND started_at < NOW() - (%s || ' hours')::interval
                RETURNING id""",
            (max_age_hours,),
            fetch=True,
        ) or []
        return len(rows)
    except Exception as exc:
        logger.debug("crew_tasks.cleanup_zombies failed: %s", exc)
        return 0


def list_recent(
    *, limit: int = 20, project_id: str | None = None
) -> list[dict[str, Any]]:
    """Return recent tasks (newest first), optionally scoped to a project.

    Matches the shape the old Firestore reader produced so the dashboard
    endpoint can drop this in without touching the TypeScript types:
    `id, crew, summary, state, started_at, eta, completed_at,
     result_preview, parent_task_id, is_sub_agent, model, project_id,
     delegated_to, delegated_from, tokens_used, cost_usd, error,
     sub_agent_progress, heal_detail`.
    """
    where = "TRUE"
    params: list[Any] = []
    if project_id:
        where = "project_id = %s"
        params.append(project_id)
    params.append(limit)
    try:
        rows = execute(
            f"""SELECT id, crew, summary, state,
                       to_char(started_at,   'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') AS started_at,
                       to_char(eta,          'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') AS eta,
                       to_char(completed_at, 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') AS completed_at,
                       to_char(last_updated, 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') AS last_updated,
                       result_preview, parent_task_id, is_sub_agent,
                       model, project_id::text AS project_id,
                       delegated_to, delegated_from,
                       tokens_used, cost_usd, error,
                       sub_agent_progress, heal_detail
                  FROM control_plane.crew_tasks
                 WHERE {where}
                 ORDER BY started_at DESC
                 LIMIT %s""",
            tuple(params), fetch=True,
        ) or []
        # Numeric → float so JSON serialises cleanly (audit/cost endpoints
        # already do this elsewhere; keep the convention).
        for r in rows:
            if r.get("cost_usd") is not None:
                try:
                    r["cost_usd"] = float(r["cost_usd"])
                except Exception:
                    pass
        return rows
    except Exception as exc:
        logger.debug("crew_tasks.list_recent failed: %s", exc)
        return []


def crew_statuses() -> list[dict[str, Any]]:
    """Return the latest state per crew, derived from the most recent
    task per `crew`. Result shape matches the legacy `crews` collection
    reader:  `{name, state, current_task, task_id, started_at,
    last_updated, eta, model, project_id}`.

    Idle crews (no recent row) are intentionally not returned — the
    dashboard endpoint merges this against the canonical CREW_REGISTRY
    so every known crew still shows up.
    """
    try:
        rows = execute(
            """SELECT DISTINCT ON (crew)
                      crew AS name,
                      CASE WHEN state = 'running' THEN 'active' ELSE 'idle' END AS state,
                      CASE WHEN state = 'running' THEN summary ELSE NULL END AS current_task,
                      CASE WHEN state = 'running' THEN id      ELSE NULL END AS task_id,
                      to_char(started_at,   'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') AS started_at,
                      to_char(last_updated, 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') AS last_updated,
                      CASE WHEN state = 'running'
                           THEN to_char(eta, 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')
                           ELSE NULL END AS eta,
                      model,
                      project_id::text AS project_id
                 FROM control_plane.crew_tasks
                ORDER BY crew, started_at DESC""",
            fetch=True,
        ) or []
        return rows
    except Exception as exc:
        logger.debug("crew_tasks.crew_statuses failed: %s", exc)
        return []
