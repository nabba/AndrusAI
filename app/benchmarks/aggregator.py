"""Rolling stats over benchmark runs (Phase C.3, 2026-05-22).

The aggregator is the read side of the benchmark suite. It walks the
JSONL store, filters by a time window + optional task / model masks,
and computes the operator-facing leaderboard.

Three views the React dashboard renders:

  * **Per-model**: mean score, pass rate, p50/p95 latency, total cost.
  * **Per-task**: which task is the hardest right now (lowest mean
    across all models).
  * **Per-(task, model)**: the matrix view — "how good is model X at
    task Y?". This is the leaderboard's main surface.

All computations are pure-function over a list of runs. No state, no
caching beyond what the caller chooses to do. At v1 scale (a few
thousand rows max) a full scan is negligible.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from app.benchmarks.models import BenchmarkRun


def _parse_ts(ts: str) -> Optional[datetime]:
    """Parse an ISO8601 timestamp, returning None for malformed input."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def filter_runs(
    runs: Iterable[BenchmarkRun],
    *,
    window_days: Optional[int] = None,
    task_id: Optional[str] = None,
    model: Optional[str] = None,
    include_errors: bool = True,
) -> list[BenchmarkRun]:
    """Filter ``runs`` by window + dimensions.

    ``window_days=None`` returns all rows; otherwise rows older than
    that many days are dropped.

    ``include_errors=False`` drops rows with non-empty ``error`` — useful
    when computing mean score (errors would zero the mean even though
    the model didn't really fail at the task).
    """
    cutoff: Optional[datetime] = None
    if window_days is not None and window_days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    out: list[BenchmarkRun] = []
    for r in runs:
        if cutoff is not None:
            ts = _parse_ts(r.ts)
            if ts is None or ts < cutoff:
                continue
        if task_id is not None and r.task_id != task_id:
            continue
        if model is not None and r.model != model:
            continue
        if not include_errors and r.error:
            continue
        out.append(r)
    return out


def _percentile(values: list[float], p: float) -> float:
    """Cheap percentile — linear interpolation between sorted samples.

    p in [0, 100]. Empty list → 0.0.
    """
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    s = sorted(values)
    rank = (p / 100.0) * (len(s) - 1)
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return s[lo]
    frac = rank - lo
    return s[lo] + (s[hi] - s[lo]) * frac


def summarise(runs: list[BenchmarkRun]) -> dict:
    """Roll up a flat list of runs into a single summary dict.

    Used by every higher-level grouping — per-model, per-task,
    per-(task, model).
    """
    if not runs:
        return {
            "n": 0,
            "n_passed": 0,
            "n_errored": 0,
            "mean_score": 0.0,
            "pass_rate": 0.0,
            "error_rate": 0.0,
            "p50_latency_ms": 0,
            "p95_latency_ms": 0,
            "total_cost_usd": 0.0,
            "total_tokens_in": 0,
            "total_tokens_out": 0,
        }
    n = len(runs)
    n_passed = sum(1 for r in runs if r.passed)
    n_errored = sum(1 for r in runs if r.error)
    # Mean score over all runs (errors count as 0 — they did, in fact,
    # fail to pass the task, whatever the cause).
    mean_score = sum(r.score for r in runs) / n
    latencies = [float(r.latency_ms) for r in runs]
    return {
        "n": n,
        "n_passed": n_passed,
        "n_errored": n_errored,
        "mean_score": round(mean_score, 4),
        "pass_rate": round(n_passed / n, 4),
        "error_rate": round(n_errored / n, 4),
        "p50_latency_ms": int(_percentile(latencies, 50.0)),
        "p95_latency_ms": int(_percentile(latencies, 95.0)),
        "total_cost_usd": round(sum(r.cost_usd for r in runs), 6),
        "total_tokens_in": sum(r.tokens_in for r in runs),
        "total_tokens_out": sum(r.tokens_out for r in runs),
    }


def per_model(
    runs: list[BenchmarkRun],
) -> dict[str, dict]:
    """Group runs by model, summarise each group.

    Returns ``{model: summary}`` sorted by mean_score descending in the
    React layer — here we just return a dict.
    """
    by_model: dict[str, list[BenchmarkRun]] = {}
    for r in runs:
        by_model.setdefault(r.model, []).append(r)
    return {model: summarise(rs) for model, rs in by_model.items()}


def per_task(
    runs: list[BenchmarkRun],
) -> dict[str, dict]:
    """Group runs by task. Useful for "which task is hardest?"."""
    by_task: dict[str, list[BenchmarkRun]] = {}
    for r in runs:
        by_task.setdefault(r.task_id, []).append(r)
    return {task_id: summarise(rs) for task_id, rs in by_task.items()}


def per_task_and_model(
    runs: list[BenchmarkRun],
) -> dict[tuple[str, str], dict]:
    """The matrix view: (task_id, model) → summary.

    Keys are tuples; the REST layer flattens to ``"task::model"`` for
    JSON serialisation.
    """
    cell: dict[tuple[str, str], list[BenchmarkRun]] = {}
    for r in runs:
        cell.setdefault((r.task_id, r.model), []).append(r)
    return {key: summarise(rs) for key, rs in cell.items()}


def leaderboard(
    runs: list[BenchmarkRun],
    *,
    window_days: Optional[int] = 7,
) -> dict:
    """One-shot leaderboard payload for the REST + React layer.

    Returns three keys:
      * ``window_days`` — the filter applied
      * ``by_model`` — model -> summary, sorted by mean_score
      * ``by_task`` — task_id -> summary, sorted by ID
      * ``matrix`` — {"task_id::model": summary} flat for JSON

    All numerical fields are pre-rounded to keep the JSON compact.
    """
    filtered = filter_runs(runs, window_days=window_days)
    by_model = per_model(filtered)
    by_task = per_task(filtered)
    matrix = per_task_and_model(filtered)

    # Sort by mean_score descending in the response — operator scans
    # leaderboard top-to-bottom looking for the winner.
    by_model_sorted = dict(
        sorted(
            by_model.items(),
            key=lambda kv: kv[1]["mean_score"],
            reverse=True,
        )
    )
    matrix_flat = {
        f"{task}::{model}": summary
        for (task, model), summary in matrix.items()
    }
    return {
        "window_days": window_days,
        "n_runs": len(filtered),
        "by_model": by_model_sorted,
        "by_task": dict(sorted(by_task.items())),
        "matrix": matrix_flat,
    }


__all__ = [
    "filter_runs",
    "leaderboard",
    "per_model",
    "per_task",
    "per_task_and_model",
    "summarise",
]
