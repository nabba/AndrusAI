"""Crew-task span persistence — fine-grained execution-flow tracking.

One span per sub-event inside a crew task (agent execution, tool call,
LLM call). The envelope (crew start/complete) is still tracked by
``crew_tasks``; this module captures what happens *inside* a crew run.

Write path: called from the CrewAI event subscribers in
``app/crews/span_events.py``. Reads: dashboard timeline endpoint.

All writes are fire-and-forget friendly — a failure is logged at DEBUG
and never raises, so crew execution isn't coupled to telemetry.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.control_plane.db import execute, execute_one

logger = logging.getLogger(__name__)

# Cap the per-row detail JSON payload. Tool inputs/outputs can be huge
# (an entire search result blob, a file's contents) and we don't want
# a single span to OOM the table. 8 KB is generous for a flow view.
_DETAIL_BUDGET_BYTES = 8 * 1024


def _truncate_detail(detail: dict[str, Any] | None) -> dict[str, Any]:
    """Cap the detail JSON at ~8 KB. Oversized values get a marker."""
    if not detail:
        return {}
    try:
        raw = json.dumps(detail, default=str)
    except Exception:
        return {"_detail_error": "unserialisable"}
    if len(raw) <= _DETAIL_BUDGET_BYTES:
        return detail
    # Shrink: keep keys, truncate string values to ~256 chars each.
    shrunk: dict[str, Any] = {}
    for k, v in detail.items():
        if isinstance(v, str) and len(v) > 256:
            shrunk[k] = v[:256] + "…(truncated)"
        else:
            shrunk[k] = v
    return shrunk


def start_span(
    *,
    task_id: str,
    span_type: str,
    name: str,
    parent_span_id: int | None = None,
    crewai_event_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> int | None:
    """Insert a new running span. Returns the span_id, or None on failure.

    ``span_type`` must be one of 'agent' / 'tool' / 'llm_call'. Unknown
    values are silently dropped — the CHECK constraint would reject them
    at the DB anyway.
    """
    if span_type not in ("agent", "tool", "llm_call"):
        logger.debug("crew_task_spans: ignoring unknown span_type=%r", span_type)
        return None
    try:
        row = execute_one(
            """
            INSERT INTO control_plane.crew_task_spans
                   (task_id, parent_span_id, span_type, name,
                    crewai_event_id, detail)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            RETURNING id
            """,
            (
                task_id,
                parent_span_id,
                span_type,
                (name or "")[:500],
                crewai_event_id,
                json.dumps(_truncate_detail(detail)),
            ),
        )
        return row["id"] if row else None
    except Exception as exc:
        logger.debug("crew_task_spans.start_span failed: %s", exc)
        return None


def complete_span(
    *,
    span_id: int,
    state: str = "completed",
    detail_patch: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Mark a span complete. ``detail_patch`` merges into the existing
    JSONB payload (e.g. adding token usage, output preview).

    No-op if ``span_id`` is falsy — callers often pass the result of a
    previous ``start_span`` call that could have failed.
    """
    if not span_id:
        return
    if state not in ("completed", "failed"):
        state = "completed"
    try:
        if detail_patch:
            # Merge patch into existing JSONB via ``||`` — keeps the
            # span_type/name intact and appends token/error fields.
            execute(
                """
                UPDATE control_plane.crew_task_spans
                   SET state        = %s,
                       completed_at = NOW(),
                       detail       = detail || %s::jsonb,
                       error        = COALESCE(%s, error)
                 WHERE id = %s
                """,
                (
                    state,
                    json.dumps(_truncate_detail(detail_patch)),
                    (error or "")[:2000] or None,
                    span_id,
                ),
            )
        else:
            execute(
                """
                UPDATE control_plane.crew_task_spans
                   SET state        = %s,
                       completed_at = NOW(),
                       error        = COALESCE(%s, error)
                 WHERE id = %s
                """,
                (state, (error or "")[:2000] or None, span_id),
            )
    except Exception as exc:
        logger.debug("crew_task_spans.complete_span failed: %s", exc)


def list_spans(task_id: str) -> list[dict]:
    """Return every span for ``task_id`` ordered by started_at ASC.

    The caller builds the tree in Python — cheaper than a recursive CTE
    for the modest span counts we see (~20 per task).
    """
    try:
        rows = execute(
            """
            SELECT id, task_id, parent_span_id, span_type, name,
                   crewai_event_id, started_at, completed_at,
                   state, detail, error
              FROM control_plane.crew_task_spans
             WHERE task_id = %s
          ORDER BY started_at ASC, id ASC
            """,
            (task_id,),
            fetch=True,
        ) or []
        return rows
    except Exception as exc:
        logger.debug("crew_task_spans.list_spans failed: %s", exc)
        return []


def purge_old_spans(days: int = 7) -> int:
    """Delete spans older than ``days`` days. Returns rows removed.

    Called from the idle scheduler. The ON DELETE CASCADE from
    ``crew_tasks.id`` would also drop spans when the parent task is
    purged — this is the direct path for the far more common case of
    tasks that hang around but have old spans we no longer need.
    """
    try:
        rows = execute(
            """
            DELETE FROM control_plane.crew_task_spans
             WHERE started_at < NOW() - (%s || ' days')::interval
            """,
            (str(int(days)),),
        )
        # psycopg's execute returns row count via .rowcount; our wrapper
        # returns None for non-fetch queries. Use a follow-up SELECT if
        # we need the exact count — for now we just log success.
        return rows if isinstance(rows, int) else 0
    except Exception as exc:
        logger.debug("crew_task_spans.purge_old_spans failed: %s", exc)
        return 0
