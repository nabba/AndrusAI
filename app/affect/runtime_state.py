"""
runtime_state.py — In-process counters/timers feeding viability variables.

Some viability variables (latency_pressure, autonomy) need lifecycle event
data that isn't stored anywhere queryable. This module holds a small,
lock-protected, in-process state that lifecycle hooks update and viability.py
reads.

Phase 2 scope:
    - PRE_TASK / ON_COMPLETE → task duration tracking → latency_pressure
    - ON_DELEGATION → delegation count → autonomy

Survives only as long as the process. Restart resets to defaults — that's
fine; affect is a continuous signal anyway, not a durable record. Long-term
durable state lives in the trace.jsonl / homeostasis.json files.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field

# ── Latency tracking ────────────────────────────────────────────────────────


@dataclass
class _ActiveTask:
    task_id: str
    started_mono: float
    role: str = ""
    sla_seconds: float = 60.0


_lock = threading.Lock()
_active_tasks: dict[str, _ActiveTask] = {}
_recent_durations: deque[float] = deque(maxlen=20)        # rolling task durations (s)
_recent_sla_ratios: deque[float] = deque(maxlen=20)       # rolling duration / sla ratios


# Per-role SLA defaults (seconds). Conservative — a typical Andrus task is
# 30-90s for a simple reply, longer for crew runs. These can be tuned via
# the soft envelope in the future.
_DEFAULT_SLA: dict[str, float] = {
    "researcher": 90.0,
    "coder": 90.0,
    "writer": 60.0,
    "introspector": 45.0,
    "commander": 30.0,
}


def task_started(task_id: str, role: str = "", sla_seconds: float | None = None) -> None:
    """Mark a task as started. Called from PRE_TASK lifecycle hook."""
    with _lock:
        _active_tasks[task_id] = _ActiveTask(
            task_id=task_id,
            started_mono=time.monotonic(),
            role=role,
            sla_seconds=sla_seconds if sla_seconds is not None else _DEFAULT_SLA.get(role, 60.0),
        )


def task_completed(task_id: str) -> None:
    """Mark a task as completed. Called from ON_COMPLETE lifecycle hook."""
    with _lock:
        t = _active_tasks.pop(task_id, None)
        if t is None:
            return
        dur = time.monotonic() - t.started_mono
        _recent_durations.append(dur)
        _recent_sla_ratios.append(min(2.0, dur / max(t.sla_seconds, 1.0)))


def latency_pressure_signal() -> tuple[float, str]:
    """[0, 1] — current latency pressure.

    Combines two signals:
        - rolling mean of (duration / SLA) for the last 20 completed tasks
        - max (elapsed / SLA) across currently-active tasks

    Returns (value, source-description).
    """
    with _lock:
        # Active task pressure
        now = time.monotonic()
        active_max = 0.0
        for t in _active_tasks.values():
            elapsed = now - t.started_mono
            ratio = elapsed / max(t.sla_seconds, 1.0)
            if ratio > active_max:
                active_max = ratio

        # Rolling-window historical pressure
        rolling = (sum(_recent_sla_ratios) / len(_recent_sla_ratios)) if _recent_sla_ratios else 0.5

    # Combine: weight rolling 60%, active 40%. Map 0-1 ratio onto pressure scale
    # capped at 1.0 (so a 2× SLA blowout reads as max pressure but doesn't go higher).
    combined = 0.6 * rolling + 0.4 * active_max
    return max(0.0, min(1.0, combined)), "rolling sla-ratio + active task elapsed"


# ── Autonomy tracking ────────────────────────────────────────────────────────


_decisions = deque(maxlen=40)  # 1 = agent-decided, 0 = delegated


def decision_logged(delegated: bool) -> None:
    """Log whether the most recent step was delegated (vs agent-decided).

    Called from PRE_TASK (agent-decided=1) or ON_DELEGATION (delegated=0).
    """
    with _lock:
        _decisions.append(0 if delegated else 1)


def autonomy_signal() -> tuple[float, str]:
    """[0, 1] — fraction of recent decisions made directly vs delegated upstream."""
    with _lock:
        if not _decisions:
            return 0.55, "default (no decisions yet)"
        ratio = sum(_decisions) / len(_decisions)
    return max(0.0, min(1.0, ratio)), f"agent-decided ratio (last {len(_decisions)})"
