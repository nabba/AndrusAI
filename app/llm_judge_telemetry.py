"""Judge-evaluation telemetry — captures per-judge scores + agreement.

Records one ``judge_evaluations`` row per multi-judge scoring pass, so
the dashboard can answer:

  * "Which models are judges deeming unreliable?" (scores low across
    multiple panels)
  * "When do my judges disagree?" (high inter-rater std-dev)
  * "Is one judge consistently more lenient / strict than the others?"
    (per-judge mean over time, against the panel mean)
  * "How often did the OpenRouter fallback fire?" (used_fallback flags)

Hooked from :func:`app.llm_discovery._score_with_judges` and any other
multi-judge evaluator. Schema:
``migrations/025_judge_pins_and_evaluations.sql``.

Retention: 30 days, swept by the idle scheduler's
``judge-eval-retention`` job. Per-row size is small (a few hundred
bytes), so the cap is generous.
"""
from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)


def _safe_float(x: Any) -> float | None:
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except (TypeError, ValueError):
        return None


def _aggregate(scores: list[float]) -> tuple[float | None, float | None]:
    """Return (mean, population_stddev) with NaN-safe inputs.

    Population stddev (not sample) because the panel is the population
    we care about — not a sample of a larger one.
    """
    cleaned = [s for s in (_safe_float(s) for s in scores) if s is not None]
    if not cleaned:
        return None, None
    mean = sum(cleaned) / len(cleaned)
    if len(cleaned) < 2:
        return mean, 0.0
    variance = sum((s - mean) ** 2 for s in cleaned) / len(cleaned)
    return mean, math.sqrt(variance)


def record_evaluation(
    *,
    candidate_model: str,
    judges: list[str],
    scores: list[float],
    used_fallback: list[bool] | None = None,
    task_id: str | None = None,
    rubric: str | None = None,
    task_description: str | None = None,
) -> None:
    """Persist one multi-judge scoring pass. Fire-and-forget.

    Caller-side requirement: ``len(judges) == len(scores)`` and, when
    provided, ``len(used_fallback) == len(judges)``. Mismatched lengths
    log a warning and the row is skipped.
    """
    if len(judges) != len(scores):
        logger.warning(
            "judge_telemetry: judges/scores length mismatch (%d vs %d) — skipping",
            len(judges), len(scores),
        )
        return
    if used_fallback is None:
        used_fallback = [False] * len(judges)
    elif len(used_fallback) != len(judges):
        logger.warning(
            "judge_telemetry: used_fallback length mismatch — padding/truncating",
        )
        if len(used_fallback) < len(judges):
            used_fallback = used_fallback + [False] * (len(judges) - len(used_fallback))
        else:
            used_fallback = used_fallback[: len(judges)]

    mean, std = _aggregate(scores)
    try:
        from app.control_plane.db import execute
        execute(
            """
            INSERT INTO control_plane.judge_evaluations
                   (task_id, candidate_model, judges, scores, used_fallback,
                    mean_score, std_dev, rubric, task_description)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                task_id,
                candidate_model[:200],
                list(judges),
                [_safe_float(s) for s in scores],
                list(used_fallback),
                mean,
                std,
                (rubric or "")[:500] or None,
                (task_description or "")[:2000] or None,
            ),
        )
    except Exception as exc:
        logger.debug("judge_telemetry.record_evaluation failed: %s", exc)


def list_recent(limit: int = 50, candidate_model: str | None = None) -> list[dict]:
    """Return recent judge evaluations for the dashboard panel.

    Ordered newest-first. ``candidate_model`` filter narrows to one
    model's history (the dashboard exposes this for "drill-down on this
    candidate").
    """
    try:
        from app.control_plane.db import execute
        if candidate_model:
            return execute(
                """
                SELECT id, task_id, candidate_model, judges, scores, used_fallback,
                       mean_score, std_dev, rubric, task_description, created_at
                  FROM control_plane.judge_evaluations
                 WHERE candidate_model = %s
              ORDER BY created_at DESC
                 LIMIT %s
                """,
                (candidate_model, int(limit)),
                fetch=True,
            ) or []
        return execute(
            """
            SELECT id, task_id, candidate_model, judges, scores, used_fallback,
                   mean_score, std_dev, rubric, task_description, created_at
              FROM control_plane.judge_evaluations
          ORDER BY created_at DESC
             LIMIT %s
            """,
            (int(limit),),
            fetch=True,
        ) or []
    except Exception as exc:
        logger.debug("judge_telemetry.list_recent failed: %s", exc)
        return []


def agreement_stats(window_hours: int = 24) -> dict[str, Any]:
    """Aggregate metrics over the last ``window_hours`` for the panel header.

    Returns:
        {
          "evaluations":          int  — total panels in the window
          "mean_std_dev":         float — average std-dev across all panels
          "high_disagreement":    int  — panels where std-dev > 0.2
          "fallback_fired":       int  — total times the OpenRouter fallback was used
          "panel_size_avg":       float — average judges per panel
        }
    """
    try:
        from app.control_plane.db import execute_one
        row = execute_one(
            """
            SELECT
                COUNT(*)::int                                                AS evaluations,
                AVG(std_dev)::numeric                                        AS mean_std_dev,
                COUNT(*) FILTER (WHERE std_dev > 0.2)::int                  AS high_disagreement,
                COALESCE(SUM(
                    (SELECT COUNT(*) FROM unnest(used_fallback) AS u WHERE u)
                ), 0)::int                                                  AS fallback_fired,
                AVG(array_length(judges, 1))::numeric                       AS panel_size_avg
              FROM control_plane.judge_evaluations
             WHERE created_at > NOW() - (%s || ' hours')::interval
            """,
            (str(int(window_hours)),),
        )
        if not row:
            return {"evaluations": 0, "mean_std_dev": None, "high_disagreement": 0,
                    "fallback_fired": 0, "panel_size_avg": None}
        # Numeric columns come back as Decimal; coerce for JSON.
        return {
            "evaluations":      int(row.get("evaluations") or 0),
            "mean_std_dev":     _safe_float(row.get("mean_std_dev")),
            "high_disagreement": int(row.get("high_disagreement") or 0),
            "fallback_fired":   int(row.get("fallback_fired") or 0),
            "panel_size_avg":   _safe_float(row.get("panel_size_avg")),
        }
    except Exception as exc:
        logger.debug("judge_telemetry.agreement_stats failed: %s", exc)
        return {"evaluations": 0, "mean_std_dev": None, "high_disagreement": 0,
                "fallback_fired": 0, "panel_size_avg": None}


def purge_old_evaluations(days: int = 30) -> int:
    """Delete evaluations older than ``days`` days. Returns rows removed."""
    try:
        from app.control_plane.db import execute
        rows = execute(
            "DELETE FROM control_plane.judge_evaluations "
            "WHERE created_at < NOW() - (%s || ' days')::interval",
            (str(int(days)),),
        )
        return rows if isinstance(rows, int) else 0
    except Exception as exc:
        logger.debug("judge_telemetry.purge_old_evaluations failed: %s", exc)
        return 0
