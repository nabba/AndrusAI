"""HOT-4 visibility hook for autonomous-executor steps.

Round 2 audit follow-up (2026-05-23). HOT-4 (`app/sentience_
experiments/hot4_metacog_monitor.py`) reads metacognitive signals
from ``workspace/observability/loadable_agent_usage.jsonl`` — which
is the LoadableAgent telemetry path. The autonomous executor
dispatches steps via ``Commander.handle()`` and was structurally
invisible to HOT-4.

This module emits one step-completion row per executed step to a
parallel JSONL at ``workspace/observability/executor_step_calls.jsonl``
in the same schema HOT-4 already understands (``ts``, ``agent_id``,
``iteration``, ``model``, ``*_tokens``). HOT-4's reader is extended
to fold both paths together.

Failure isolation: a broken write NEVER blocks the executor's step
dispatch. The step has already committed to a status (COMPLETED /
FAILED) and persisted before this hook runs.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_MAX_ROWS = 10_000
_LOCK = threading.Lock()


def _workspace_root() -> Path:
    return Path(os.getenv("WORKSPACE_ROOT", "/app/workspace"))


def _telemetry_path() -> Path:
    return _workspace_root() / "observability" / "executor_step_calls.jsonl"


def emit_step_telemetry(run: Any, step: Any) -> bool:
    """Append one HOT-4-compatible row for a completed executor step.

    Returns True on success, False on any failure. Caller treats False
    as "telemetry unavailable" — never blocks the step lifecycle.

    Schema mirrors ``loadable_agent_usage.jsonl`` so HOT-4's reader
    can consume both paths uniformly:

        ts                          — step ended_at, ISO UTC
        agent_id                    — ``autonomous_executor:<run_id>``
                                      (per-run agent so HOT-4 builds
                                      per-run baselines, not one global)
        iteration                   — step.iteration (or step index)
        model                       — best-known model string
        output_tokens               — step.tokens_used (we don't have
                                      input vs output split from the
                                      executor; output approximates
                                      the "what was emitted" signal
                                      HOT-4's confidence_proxy uses)
        input_tokens                — 1 (sentinel; avoids div-by-zero)
        cache_read_input_tokens     — 0 (unknown)
        cache_creation_input_tokens — 0 (unknown)

    The agent_id is per-run so HOT-4 detects per-run baseline shifts.
    A run with 10 escalating steps shows up as a clear baseline-drift
    pattern in the HOT-4 detectors.
    """
    try:
        row = {
            "ts": getattr(step, "ended_at", None) or _now_iso(),
            "agent_id": f"autonomous_executor:{getattr(run, 'run_id', 'unknown')}",
            "iteration": int(getattr(step, "iteration", 0) or 0),
            "model": str(getattr(step, "model", "") or "?"),
            "output_tokens": int(getattr(step, "tokens_used", 0) or 0),
            "input_tokens": 1,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            # Free-form metadata for forensics. HOT-4 ignores unknown
            # fields, so this is observational-only.
            "_executor": {
                "run_id": getattr(run, "run_id", ""),
                "step_id": getattr(step, "step_id", ""),
                "status": (
                    getattr(step.status, "value", str(step.status))
                    if getattr(step, "status", None) is not None
                    else ""
                ),
                "cost_usd": float(getattr(step, "cost_usd", 0.0) or 0.0),
            },
        }
    except Exception:
        logger.debug("hot4_telemetry: row build failed", exc_info=True)
        return False

    path = _telemetry_path()
    with _LOCK:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            from app.utils.jsonl_retention import append_with_cap
            append_with_cap(path, json.dumps(row), max_lines=_MAX_ROWS)
        except Exception:
            logger.debug("hot4_telemetry: append failed", exc_info=True)
            return False
    return True


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
