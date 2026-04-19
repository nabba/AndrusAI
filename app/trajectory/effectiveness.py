"""
app.trajectory.effectiveness — Per-tip effectiveness tracking (Phase 6).

Closes the paper's feedback loop: every time a trajectory tip is
surfaced by the context_builder AND the trajectory completes, we
persist a (skill_id, outcome) pair. Aggregating these pairs tells us
which tips actually correlate with success and which should be archived.

One file, one responsibility:

    record_use(trajectory, attribution)
        Appends one row per (trajectory × injected_skill_id) to a JSONL
        log. Called from the attribution pipeline after the trajectory
        and its attribution record are both available.

    effectiveness_report(skill_record_id=None, limit=100)
        Aggregates the rolling-window log into per-tip stats
        (uses/successes/recoveries/effectiveness). When skill_record_id
        is set, returns just that tip's stats.

    top_tips(k=10), worst_tips(k=10)
        Rank active tips by effectiveness for the Evaluator and
        dashboard consumers.

Design constraints (match the rest of app.trajectory):
    * Append-only JSONL, atomic single-line writes.
    * Never raises — all entrypoints swallow errors at the outer boundary.
    * No LLM calls. Pure correlation bookkeeping.
    * Flag-gated via trajectory_enabled (Phase 1 foundation) — when
      trajectory capture is off, nothing is written.

IMMUTABLE — infrastructure-level module.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.trajectory.types import (
    Trajectory, AttributionRecord,
    VERDICT_FAILURE, VERDICT_RECOVERY, VERDICT_OPTIMIZATION, VERDICT_BASELINE,
)

logger = logging.getLogger(__name__)

_LOG_PATH = Path("/app/workspace/trajectories/tip_effectiveness.jsonl")

# Rolling window for aggregation — larger = more stable, slower to
# respond to regressions. 500 matches typical effectiveness-tracking
# buffers and corresponds to a few days of activity.
_WINDOW_SIZE = 500

# Minimum uses before effectiveness can be considered "stable enough"
# for the Evaluator to act on. Below this, report but don't act.
MIN_USES_FOR_ACTION = 10


# ── Flag check ────────────────────────────────────────────────────────

def _enabled() -> bool:
    try:
        from app.config import get_settings
        return bool(get_settings().trajectory_enabled)
    except Exception:
        return False


# ── Public API ────────────────────────────────────────────────────────

def record_use(
    trajectory: Trajectory,
    attribution: Optional[AttributionRecord] = None,
) -> int:
    """Append one effectiveness row per injected SkillRecord.

    Returns the number of rows written (0 when no tips were injected or
    the trajectory lacks outcome data). Never raises.

    The pair schema per row:
        ts, trajectory_id, skill_id, crew_name,
        passed_quality_gate, retries, reflexion_exhausted,
        verdict (if attribution available), failure_mode
    """
    if not _enabled():
        return 0
    if trajectory is None or not trajectory.injected_skill_ids:
        return 0
    try:
        outcome = trajectory.outcome_summary or {}
        if not outcome:
            return 0

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        base_row = {
            "ts": now,
            "trajectory_id": trajectory.trajectory_id,
            "crew_name": trajectory.crew_name,
            "passed_quality_gate": bool(outcome.get("passed_quality_gate", True)),
            "retries": int(outcome.get("retries", 0) or 0),
            "reflexion_exhausted": bool(outcome.get("reflexion_exhausted", False)),
            "difficulty": int(outcome.get("difficulty", 0) or 0),
            "verdict": attribution.verdict if attribution else "",
            "failure_mode": attribution.failure_mode if attribution else "",
            "attribution_confidence": (
                round(attribution.confidence, 3) if attribution else 0.0
            ),
        }

        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with _LOG_PATH.open("a", encoding="utf-8") as fh:
            for sid in trajectory.injected_skill_ids:
                if not sid:
                    continue
                row = dict(base_row)
                row["skill_id"] = sid
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                written += 1
        return written
    except Exception:
        logger.debug("effectiveness.record_use failed", exc_info=True)
        return 0


def effectiveness_report(
    skill_record_id: Optional[str] = None,
    limit: int = _WINDOW_SIZE,
) -> dict:
    """Aggregate per-tip stats from the recent window.

    Shape:
        {
          "samples": <N rows read>,
          "per_tip": {
            "<skill_id>": {
              "uses": int,
              "successes": int,
              "failures": int,
              "recoveries": int,     # verdict == recovery
              "retries_avg": float,
              "effectiveness": float # successes / uses
            },
            ...
          }
        }

    When `skill_record_id` is set, `per_tip` contains only that key.
    Never raises.
    """
    rows = _tail(limit)
    per_tip: dict[str, dict] = defaultdict(lambda: {
        "uses": 0, "successes": 0, "failures": 0, "recoveries": 0,
        "retries_sum": 0, "effectiveness": 0.0,
    })

    for r in rows:
        sid = r.get("skill_id", "")
        if not sid:
            continue
        if skill_record_id and sid != skill_record_id:
            continue
        rec = per_tip[sid]
        rec["uses"] += 1
        if r.get("passed_quality_gate"):
            rec["successes"] += 1
        else:
            rec["failures"] += 1
        if r.get("verdict") == VERDICT_RECOVERY:
            rec["recoveries"] += 1
        rec["retries_sum"] += int(r.get("retries", 0) or 0)

    # Finalise averages/ratios
    finalized: dict[str, dict] = {}
    for sid, rec in per_tip.items():
        uses = rec["uses"]
        finalized[sid] = {
            "uses": uses,
            "successes": rec["successes"],
            "failures": rec["failures"],
            "recoveries": rec["recoveries"],
            "retries_avg": round(rec["retries_sum"] / max(1, uses), 3),
            "effectiveness": round(rec["successes"] / max(1, uses), 3),
        }
    return {"samples": len(rows), "per_tip": finalized}


def top_tips(k: int = 10, min_uses: int = MIN_USES_FOR_ACTION) -> list[dict]:
    """Return the top-k tips by effectiveness with at least `min_uses` samples.

    Each entry: {skill_id, uses, successes, effectiveness}.
    Sorted by effectiveness desc, then uses desc.
    """
    rep = effectiveness_report()
    pool = [
        {"skill_id": sid, **stats}
        for sid, stats in rep.get("per_tip", {}).items()
        if stats["uses"] >= min_uses
    ]
    pool.sort(key=lambda r: (r["effectiveness"], r["uses"]), reverse=True)
    return pool[:k]


def worst_tips(k: int = 10, min_uses: int = MIN_USES_FOR_ACTION) -> list[dict]:
    """Dual of top_tips — bottom-k. Used by the Evaluator to archive dead tips."""
    rep = effectiveness_report()
    pool = [
        {"skill_id": sid, **stats}
        for sid, stats in rep.get("per_tip", {}).items()
        if stats["uses"] >= min_uses
    ]
    pool.sort(key=lambda r: (r["effectiveness"], -r["uses"]))
    return pool[:k]


# ── Internal: bounded-window JSONL read ───────────────────────────────

def _tail(n: int) -> list[dict]:
    if not _LOG_PATH.exists():
        return []
    out: list[dict] = []
    try:
        buf: deque[str] = deque(maxlen=n)
        with _LOG_PATH.open("r", encoding="utf-8") as fh:
            for line in fh:
                buf.append(line)
        for line in buf:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        logger.debug("effectiveness._tail failed", exc_info=True)
    return out
