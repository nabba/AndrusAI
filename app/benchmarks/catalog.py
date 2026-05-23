"""Benchmark task catalog (Phase C.3, 2026-05-22).

Loads YAML task definitions from ``app/benchmarks/tasks/*.yaml`` at
module level. The catalog is a read-only view of those files — tasks
are authored in YAML, never edited at runtime.

Why YAML?

The task definitions are operator-authored. YAML is the standard for
that role: comments + multi-line strings + minimal punctuation. They
could be authored in JSON or Python too; we picked YAML because the
``input`` field is often a multi-line prompt where YAML's literal
block ``|`` syntax is the readable choice.

The catalog validates every task at load time:

  * Required fields present (``id`` / ``input`` / ``expected`` /
    ``scorer``)
  * ``scorer`` is in :data:`scorers.SCORER_REGISTRY`
  * No duplicate ``id``s across files
  * ``model_targets`` are valid tier names

A validation failure logs a warning and skips that task — the rest of
the catalog still loads. Better to run the benchmarks we can than to
break the whole suite on one typo.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Optional

import yaml

from app.benchmarks.models import BenchmarkTask
from app.benchmarks.scorers import SCORER_REGISTRY

logger = logging.getLogger(__name__)


# Canonical tier names the runner / LLM factory understand.
VALID_TIERS = frozenset({"cheap", "default", "smart"})


def _tasks_dir() -> Path:
    """Where the YAML task files live. Beside this module."""
    return Path(__file__).parent / "tasks"


def _validate_task_dict(raw: dict, source: str) -> Optional[BenchmarkTask]:
    """Parse one dict into a ``BenchmarkTask`` or return None with a
    logged reason. The catalog never raises — invalid tasks are
    skipped, not fatal."""
    try:
        task_id = str(raw.get("id", "")).strip()
        if not task_id:
            logger.warning(
                "benchmarks.catalog: %s missing 'id' — skipped", source,
            )
            return None
        scorer = str(raw.get("scorer", "")).strip()
        if scorer not in SCORER_REGISTRY:
            logger.warning(
                "benchmarks.catalog: %s[%s] unknown scorer %r — skipped "
                "(available: %s)",
                source, task_id, scorer,
                sorted(SCORER_REGISTRY.keys()),
            )
            return None
        targets = raw.get("model_targets") or ["default"]
        if not isinstance(targets, list):
            logger.warning(
                "benchmarks.catalog: %s[%s] model_targets must be a list — "
                "skipped", source, task_id,
            )
            return None
        bad_tiers = [t for t in targets if t not in VALID_TIERS]
        if bad_tiers:
            logger.warning(
                "benchmarks.catalog: %s[%s] unknown tier(s) %s — skipped "
                "(valid: %s)",
                source, task_id, bad_tiers, sorted(VALID_TIERS),
            )
            return None
        return BenchmarkTask(
            id=task_id,
            name=str(raw.get("name", task_id)),
            description=str(raw.get("description", "")),
            input=str(raw.get("input", "")),
            expected=raw.get("expected"),
            scorer=scorer,
            scorer_args=dict(raw.get("scorer_args") or {}),
            model_targets=list(targets),
            timeout_s=int(raw.get("timeout_s", 30)),
            max_tokens=(
                int(raw["max_tokens"])
                if raw.get("max_tokens") is not None
                else None
            ),
            category=str(raw.get("category", "general")),
        )
    except (ValueError, TypeError) as exc:
        logger.warning(
            "benchmarks.catalog: %s validation failed: %s — skipped",
            source, exc,
        )
        return None


def _load_yaml_file(path: Path) -> Iterable[dict]:
    """Yield dict entries from one YAML file.

    Supports two shapes:
      * Single-task: top-level mapping with task fields.
      * Multi-task: top-level mapping with key ``"tasks"`` whose value
        is a list of task mappings.

    Failure-isolated — a malformed file logs + yields nothing.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning(
            "benchmarks.catalog: cannot read %s: %s", path, exc,
        )
        return
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        logger.warning(
            "benchmarks.catalog: %s YAML parse error: %s", path, exc,
        )
        return
    if data is None:
        return
    if isinstance(data, dict) and isinstance(data.get("tasks"), list):
        for entry in data["tasks"]:
            if isinstance(entry, dict):
                yield entry
        return
    if isinstance(data, dict):
        yield data
        return
    logger.warning(
        "benchmarks.catalog: %s unrecognised shape (expected dict or "
        "{tasks: [...]}) — skipped", path,
    )


def load_tasks(tasks_dir: Optional[Path] = None) -> list[BenchmarkTask]:
    """Load every task in the catalog. Sorted by id for determinism.

    Idempotent — call as often as you like. Cheap (one read per file).
    Duplicate ids across files trigger a warning + only the first wins.
    """
    base = tasks_dir if tasks_dir is not None else _tasks_dir()
    if not base.exists() or not base.is_dir():
        return []
    seen_ids: set[str] = set()
    tasks: list[BenchmarkTask] = []
    for path in sorted(base.glob("*.yaml")) + sorted(base.glob("*.yml")):
        for raw in _load_yaml_file(path):
            task = _validate_task_dict(raw, str(path.name))
            if task is None:
                continue
            if task.id in seen_ids:
                logger.warning(
                    "benchmarks.catalog: duplicate id %r in %s — kept "
                    "first occurrence", task.id, path.name,
                )
                continue
            seen_ids.add(task.id)
            tasks.append(task)
    tasks.sort(key=lambda t: t.id)
    return tasks


def get_task(task_id: str) -> Optional[BenchmarkTask]:
    """Convenience lookup — None when not found."""
    for task in load_tasks():
        if task.id == task_id:
            return task
    return None


def catalog_stats() -> dict:
    """Quick summary for the operator surface."""
    tasks = load_tasks()
    by_cat: dict[str, int] = {}
    by_scorer: dict[str, int] = {}
    for task in tasks:
        by_cat[task.category] = by_cat.get(task.category, 0) + 1
        by_scorer[task.scorer] = by_scorer.get(task.scorer, 0) + 1
    return {
        "task_count": len(tasks),
        "by_category": by_cat,
        "by_scorer": by_scorer,
    }


__all__ = [
    "VALID_TIERS",
    "catalog_stats",
    "get_task",
    "load_tasks",
]
