"""Per-record JSON store for executor runs. Mirrors threads/store.py.

  workspace/autonomous_executor/<run_id>.json   — full ExecutorRun

Atomic writes via tempfile + rename. The on-disk JSON is the source
of truth; the in-memory ``_INDEX`` mirrors it for fast list queries.

Terminal-state immutability is enforced at the model layer
(:meth:`ExecutorRun.transition`); this module is a passive persister.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Optional

from app.autonomous_executor.models import ExecutorRun, TERMINAL_STATUSES

logger = logging.getLogger(__name__)


_DEFAULT_BASE_DIR = Path("/app/workspace/autonomous_executor")
_base_dir_override: Path | None = None
_LOCK = threading.RLock()
_INDEX: dict[str, ExecutorRun] | None = None


def _base_dir() -> Path:
    return _base_dir_override or _DEFAULT_BASE_DIR


def get_base_dir() -> Path:
    """Expose the active base directory (used by tests + diagnostics)."""
    return _base_dir()


def _ensure_dir() -> None:
    _base_dir().mkdir(parents=True, exist_ok=True)


def _record_path(run_id: str) -> Path:
    return _base_dir() / f"{run_id}.json"


def _index() -> dict[str, ExecutorRun]:
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    with _LOCK:
        if _INDEX is not None:
            return _INDEX
        _ensure_dir()
        loaded: dict[str, ExecutorRun] = {}
        for f in _base_dir().glob("*.json"):
            try:
                run = ExecutorRun.from_dict(json.loads(f.read_text()))
                loaded[run.run_id] = run
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "autonomous_executor: cannot load %s: %s", f, exc,
                )
        _INDEX = loaded
        return _INDEX


def _persist(run: ExecutorRun) -> None:
    _ensure_dir()
    path = _record_path(run.run_id)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(run.to_dict(), indent=2, default=str))
    tmp.replace(path)


def save(run: ExecutorRun) -> None:
    """Persist + cache. Caller is responsible for state-machine
    correctness — this module does not validate transitions."""
    with _LOCK:
        idx = _index()
        idx[run.run_id] = run
        _persist(run)


def get(run_id: str) -> Optional[ExecutorRun]:
    return _index().get(run_id)


def list_all(*, limit: int = 100) -> list[ExecutorRun]:
    items = list(_index().values())
    items.sort(
        key=lambda r: r.last_touched_at or r.created_at,
        reverse=True,
    )
    return items[:limit]


def list_active(*, limit: int = 100) -> list[ExecutorRun]:
    """Runs that have not reached a terminal state.

    Order: most recently touched first (matches the threads convention).
    """
    items = [
        r for r in _index().values()
        if r.status not in TERMINAL_STATUSES
    ]
    items.sort(
        key=lambda r: r.last_touched_at or r.created_at,
        reverse=True,
    )
    return items[:limit]


def list_terminal(*, limit: int = 100) -> list[ExecutorRun]:
    """Runs in a terminal state (completed / failed / aborted /
    budget-exhausted). Ordered most-recently-ended first."""
    items = [
        r for r in _index().values()
        if r.status in TERMINAL_STATUSES
    ]
    items.sort(
        key=lambda r: r.ended_at or r.last_touched_at or r.created_at,
        reverse=True,
    )
    return items[:limit]


def reset_for_tests(base_dir: Path | None = None) -> None:
    """Test helper — clears the in-memory cache and optionally
    redirects the base dir to a tmp path. Never call from production."""
    global _INDEX, _base_dir_override
    with _LOCK:
        _INDEX = None
        _base_dir_override = base_dir
