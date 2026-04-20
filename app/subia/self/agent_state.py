"""
agent_state.py — Per-agent runtime statistics tracker.

Tracks task counts, success rates, confidence distributions, and per-task-type
performance for each crew. Used by:
  - L1 Self-Model: live runtime stats in agent backstory
  - L5 Theory of Mind: which crew has best success rate for a task type
  - L4 Autobiographical Memory: feeds into system chronicle

NOTE ON CONFIDENCE: The `avg_confidence` value here (0.0-1.0) is a PER-AGENT
rolling average derived from self-report confidence levels after each task
(low=0.3, medium=0.5, high=0.7). It is DIFFERENT from the system-wide
confidence in homeostasis.py:
  - agent_state.avg_confidence: per-agent, tracks individual crew performance
  - homeostasis.confidence: system-wide proto-emotional signal, affects behavior
Both are valid metrics at different scopes — they are NOT expected to match.

Storage: Single JSON file at workspace/agent_state.json.
Writes are atomic (temp file + rename) to prevent corruption.
Updated by the existing _post_crew_telemetry() hook — no new threads.
"""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_STATE_PATH = Path("/app/workspace/agent_state.json")


def _load() -> dict:
    """Load agent state from disk. Returns empty dict if not found."""
    try:
        if _STATE_PATH.exists():
            return json.loads(_STATE_PATH.read_text())
    except Exception:
        logger.debug("agent_state: load failed", exc_info=True)
    return {}


def _save(state: dict) -> None:
    """Atomic write via shared safe_io utility."""
    try:
        from app.safe_io import safe_write_json
        safe_write_json(_STATE_PATH, state)
    except Exception:
        logger.debug("agent_state: save failed", exc_info=True)


def record_task(
    crew_name: str,
    success: bool,
    confidence: str = "medium",
    difficulty: int = 5,
    duration_s: float = 0.0,
) -> None:
    """Record a task completion/failure for a crew."""
    state = _load()
    agent = state.setdefault(crew_name, {
        "tasks_completed": 0,
        "tasks_failed": 0,
        "success_rate": 0.0,
        "avg_confidence": 0.5,
        "by_difficulty": {},
        "streak": 0,
        "last_task_at": "",
    })

    if success:
        agent["tasks_completed"] = agent.get("tasks_completed", 0) + 1
        agent["streak"] = agent.get("streak", 0) + 1
    else:
        agent["tasks_failed"] = agent.get("tasks_failed", 0) + 1
        agent["streak"] = 0

    total = agent["tasks_completed"] + agent["tasks_failed"]
    agent["success_rate"] = round(agent["tasks_completed"] / total, 4) if total else 0.0

    # Rolling average confidence (map string to float)
    conf_map = {"high": 0.9, "medium": 0.6, "low": 0.2}
    conf_val = conf_map.get(confidence, 0.5)
    alpha = 0.1  # exponential moving average
    agent["avg_confidence"] = round(
        alpha * conf_val + (1 - alpha) * agent.get("avg_confidence", 0.5), 4
    )

    # Per-difficulty tracking
    d_key = str(difficulty)
    by_d = agent.setdefault("by_difficulty", {})
    d_stats = by_d.setdefault(d_key, {"completed": 0, "failed": 0})
    if success:
        d_stats["completed"] += 1
    else:
        d_stats["failed"] += 1

    agent["last_task_at"] = datetime.now(timezone.utc).isoformat()
    _save(state)


def get_agent_stats(crew_name: str) -> dict:
    """Get runtime stats for a specific crew. Returns {} if no data."""
    state = _load()
    return state.get(crew_name, {})


def get_all_stats() -> dict:
    """Get stats for all crews."""
    return _load()


# Crew names that are allowed to win the Theory-of-Mind vote.  Anything
# else — test fixtures like 'tom_test_research', stale evaluation-harness
# entries, typos introduced by self-improvement, etc. — is rejected.
# Must match the set of crews that Commander.handle can actually dispatch
# to (see `crew_name MUST be one of:` in ROUTING_PROMPT).
_VALID_CREWS: frozenset[str] = frozenset({
    "research", "coding", "writing", "media", "creative",
    "pim", "financial", "desktop", "repo_analysis", "devops",
    "critic",
    # commander isn't a dispatchable crew but sometimes appears in stats
    # for direct responses; excluded so ToM can't recommend it.
})


def get_best_crew_for_difficulty(difficulty: int) -> str | None:
    """Theory of Mind: which crew has the best success rate at this difficulty?

    Guarded so only KNOWN dispatchable crews can win.  Without this guard,
    stale test fixtures in agent_state.json (e.g. 'tom_test_research' with
    a planted 5/0 record) hijack real user requests — Commander dispatches
    to a crew that doesn't exist, execution falls through, and the task
    description gets echoed back to the user as the 'answer'.
    """
    state = _load()
    best_crew = None
    best_rate = 0.0
    d_key = str(difficulty)

    for crew_name, agent in state.items():
        if crew_name not in _VALID_CREWS:
            continue  # reject phantom crews (test fixtures / harness leftovers)
        by_d = agent.get("by_difficulty", {})
        d_stats = by_d.get(d_key, {})
        total = d_stats.get("completed", 0) + d_stats.get("failed", 0)
        if total >= 3:  # need at least 3 samples
            rate = d_stats["completed"] / total
            if rate > best_rate:
                best_rate = rate
                best_crew = crew_name

    return best_crew


def prune_phantom_crews() -> int:
    """Remove crews from agent_state.json that aren't in _VALID_CREWS.

    Called at startup to scrub stale test fixtures and harness leftovers
    so they don't resurface after a restart.  Returns the number of
    entries removed.
    """
    state = _load()
    before = len(state)
    to_remove = [k for k in state if k not in _VALID_CREWS]
    if not to_remove:
        return 0
    for k in to_remove:
        del state[k]
    _save(state)
    return before - len(state)
