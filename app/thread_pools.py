"""
thread_pools.py — Named, shared ThreadPoolExecutors.

Replaces ~12 ad-hoc pool constructions scattered across main.py,
idle_scheduler.py, orchestrator.py, firebase/infra.py, and others.

Pools are keyed by name so repeat calls from different modules reuse
the same executor. All pools shut down cleanly at process exit via
an atexit hook — no thread leaks on restart.

Usage:
    from app.thread_pools import commander_pool, ctx_pool

    pool = commander_pool()
    fut = pool.submit(fn, *args)

    # Or generic named pool
    from app.thread_pools import get_pool
    pool = get_pool("evolution", max_workers=2)

The first call to a named pool determines its max_workers. Subsequent
calls with different max_workers are ignored — this is intentional,
so that the pool size is owned by the application wiring, not by
whichever module happens to call first.
"""

from __future__ import annotations

import atexit
import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Dict

logger = logging.getLogger(__name__)

_pools: Dict[str, ThreadPoolExecutor] = {}
_lock = Lock()


def get_pool(name: str, max_workers: int = 4) -> ThreadPoolExecutor:
    """Get-or-create a named pool. Thread-safe and idempotent by name.

    Args:
        name:         Logical identifier for the pool. Used as thread name
                      prefix ("app-<name>-…") for easier debugging.
        max_workers:  Honored only at first-call time; ignored thereafter.
    """
    with _lock:
        pool = _pools.get(name)
        if pool is None:
            pool = ThreadPoolExecutor(
                max_workers=max_workers,
                thread_name_prefix=f"app-{name}",
            )
            _pools[name] = pool
            logger.debug("thread_pools: created pool '%s' (max_workers=%d)",
                         name, max_workers)
        return pool


# ── Named pools for well-known call sites ──────────────────────────

def commander_pool(max_workers: int = 10) -> ThreadPoolExecutor:
    """Pool for the Commander's task dispatch (main.py:54)."""
    return get_pool("commander", max_workers)


def ctx_pool(max_workers: int = 4) -> ThreadPoolExecutor:
    """Pool for parallel context fetching (orchestrator.py:49)."""
    return get_pool("ctx", max_workers)


def firebase_pool(max_workers: int = 8) -> ThreadPoolExecutor:
    """Pool for Firestore writes (firebase/infra.py:28)."""
    return get_pool("firebase", max_workers)


def idle_light_pool(max_workers: int = 3) -> ThreadPoolExecutor:
    """Pool for lightweight idle jobs (idle_scheduler.py:247)."""
    return get_pool("idle-light", max_workers)


def parallel_crew_pool(max_workers: int = 4) -> ThreadPoolExecutor:
    """Pool for parallel crew runs (crews/parallel_runner.py:27)."""
    return get_pool("parallel-crew", max_workers)


# ── Lifecycle ──────────────────────────────────────────────────────

def shutdown_all(wait: bool = False) -> None:
    """Shutdown every named pool. Called automatically at exit."""
    with _lock:
        for name, pool in _pools.items():
            try:
                pool.shutdown(wait=wait)
            except Exception:
                logger.debug("thread_pools: error shutting down '%s'", name,
                             exc_info=True)
        _pools.clear()


atexit.register(shutdown_all)
