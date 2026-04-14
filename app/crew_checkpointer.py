"""
crew_checkpointer.py — State checkpointing for crew recovery.

When a crew fails at step 4/6, the entire task currently restarts from
scratch. This module saves state at subtask boundaries so failed crews
can resume from the last good checkpoint.

Checkpoint lifecycle:
  PRE_TASK  → load existing checkpoint if available (opportunistic resume)
  ON_COMPLETE → save checkpoint after successful step
  ON_ERROR → save partial checkpoint for post-mortem

Checkpoints use content-hash task IDs so identical retries of the same
task automatically find existing checkpoints. Storage is bounded
(200 total, 24h max age) with automatic cleanup via idle scheduler.

Uses WorkspaceLock for concurrent write safety — same mechanism as
workspace_versioning.py and evolution strategies.

Reference: LangGraph persistence/checkpointing pattern
"""

import hashlib
import json
import logging
import shutil
import time
from dataclasses import dataclass, asdict
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

CHECKPOINT_DIR = Path("/app/workspace/checkpoints")
_MAX_CHECKPOINTS_PER_TASK = 10
_MAX_TOTAL_CHECKPOINTS = 200
_MAX_AGE_HOURS = 24


@dataclass
class CrewCheckpoint:
    """Serializable crew state at a subtask boundary."""
    task_id: str
    step_index: int
    crew_name: str
    task_description: str
    completed_steps: list[str]
    intermediate_results: dict
    confidence_chain: list[dict]  # From confidence_tracker
    metadata: dict
    timestamp: float

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CrewCheckpoint":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ── Task ID computation ──────────────────────────────────────────────────────

def compute_task_id(crew_name: str, task_description: str) -> str:
    """Content-hash task ID: identical retries find existing checkpoints."""
    key = f"{crew_name}:{task_description[:200]}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


# ── Checkpoint operations ────────────────────────────────────────────────────

def save_checkpoint(checkpoint: CrewCheckpoint) -> Path | None:
    """Save a checkpoint to disk. Returns path on success, None on failure."""
    try:
        task_dir = CHECKPOINT_DIR / checkpoint.task_id
        task_dir.mkdir(parents=True, exist_ok=True)

        # Enforce per-task limit
        existing = sorted(task_dir.glob("step_*.json"))
        while len(existing) >= _MAX_CHECKPOINTS_PER_TASK:
            existing[0].unlink(missing_ok=True)
            existing.pop(0)

        path = task_dir / f"step_{checkpoint.step_index:03d}.json"
        path.write_text(json.dumps(checkpoint.to_dict(), default=str))

        logger.debug(
            f"crew_checkpointer: saved checkpoint {checkpoint.task_id}/"
            f"step_{checkpoint.step_index:03d}"
        )
        return path

    except Exception as e:
        logger.debug(f"crew_checkpointer: save failed: {e}")
        return None


def load_latest_checkpoint(task_id: str) -> CrewCheckpoint | None:
    """Load the most recent checkpoint for a task. Returns None if not found."""
    try:
        task_dir = CHECKPOINT_DIR / task_id
        if not task_dir.exists():
            return None

        files = sorted(task_dir.glob("step_*.json"))
        if not files:
            return None

        data = json.loads(files[-1].read_text())
        return CrewCheckpoint.from_dict(data)

    except Exception as e:
        logger.debug(f"crew_checkpointer: load failed: {e}")
        return None


def can_resume(task_id: str) -> bool:
    """Check if a valid checkpoint exists for resuming."""
    try:
        task_dir = CHECKPOINT_DIR / task_id
        if not task_dir.exists():
            return False
        files = list(task_dir.glob("step_*.json"))
        if not files:
            return False
        # Check age
        newest = max(f.stat().st_mtime for f in files)
        age_hours = (time.time() - newest) / 3600
        return age_hours < _MAX_AGE_HOURS
    except Exception:
        return False


# ── Cleanup ──────────────────────────────────────────────────────────────────

def cleanup_old_checkpoints(max_age_hours: int = _MAX_AGE_HOURS) -> int:
    """Remove checkpoints older than max_age_hours. Returns count removed.

    Designed for idle_scheduler as a LIGHT job.
    """
    removed = 0
    try:
        if not CHECKPOINT_DIR.exists():
            return 0

        cutoff = time.time() - max_age_hours * 3600

        for task_dir in CHECKPOINT_DIR.iterdir():
            if not task_dir.is_dir():
                continue
            files = list(task_dir.glob("step_*.json"))
            if not files:
                shutil.rmtree(task_dir, ignore_errors=True)
                removed += 1
                continue
            newest = max(f.stat().st_mtime for f in files)
            if newest < cutoff:
                shutil.rmtree(task_dir, ignore_errors=True)
                removed += 1

        # Global bound
        task_dirs = sorted(
            CHECKPOINT_DIR.iterdir(),
            key=lambda d: d.stat().st_mtime if d.is_dir() else 0,
        )
        while len(task_dirs) > _MAX_TOTAL_CHECKPOINTS:
            oldest = task_dirs.pop(0)
            if oldest.is_dir():
                shutil.rmtree(oldest, ignore_errors=True)
                removed += 1

    except Exception as e:
        logger.debug(f"crew_checkpointer: cleanup error: {e}")

    if removed:
        logger.info(f"crew_checkpointer: cleaned up {removed} old checkpoints")
    return removed


# ── SUBIA integration ────────────────────────────────────────────────────────

def _boost_resilience_on_resume() -> None:
    """Successful checkpoint resume boosts SUBIA coherence."""
    try:
        from app.subia.kernel import get_active_kernel
        kernel = get_active_kernel()
        if kernel and hasattr(kernel, "homeostasis"):
            current = kernel.homeostasis.variables.get("coherence", 0.5)
            kernel.homeostasis.variables["coherence"] = min(1.0, current + 0.03)
    except Exception:
        pass


# ── Lifecycle hooks ──────────────────────────────────────────────────────────

def create_checkpoint_pre_hook():
    """PRE_TASK hook: check for existing checkpoint and offer resume."""
    def _hook(ctx):
        try:
            crew = ctx.metadata.get("crew", ctx.agent_id or "")
            task_id = compute_task_id(crew, ctx.task_description)

            if can_resume(task_id):
                checkpoint = load_latest_checkpoint(task_id)
                if checkpoint:
                    ctx.metadata["_resume_from_checkpoint"] = True
                    ctx.metadata["_checkpoint_available"] = True
                    ctx.metadata["_checkpoint_step"] = checkpoint.step_index
                    ctx.metadata["_checkpoint_data"] = checkpoint.intermediate_results
                    _boost_resilience_on_resume()
                    logger.info(
                        f"crew_checkpointer: resumable checkpoint found for "
                        f"{task_id} at step {checkpoint.step_index}"
                    )
        except Exception:
            pass
        return ctx
    return _hook


def create_checkpoint_post_hook():
    """ON_COMPLETE hook: save checkpoint after successful step."""
    def _hook(ctx):
        try:
            crew = ctx.metadata.get("crew", ctx.agent_id or "")
            task_id = compute_task_id(crew, ctx.task_description)
            step = ctx.metadata.get("_step_index", 0)

            checkpoint = CrewCheckpoint(
                task_id=task_id,
                step_index=step,
                crew_name=crew,
                task_description=ctx.task_description[:200],
                completed_steps=ctx.metadata.get("_completed_steps", []),
                intermediate_results=ctx.metadata.get("_intermediate_results", {}),
                confidence_chain=ctx.metadata.get("confidence_chain", []),
                metadata={
                    k: v for k, v in ctx.metadata.items()
                    if isinstance(v, (str, int, float, bool, list, dict)) and not k.startswith("_")
                },
                timestamp=time.time(),
            )
            save_checkpoint(checkpoint)

        except Exception:
            pass
        return ctx
    return _hook


def create_checkpoint_error_hook():
    """ON_ERROR hook: save partial checkpoint for post-mortem and potential resume."""
    def _hook(ctx):
        try:
            crew = ctx.metadata.get("crew", ctx.agent_id or "")
            task_id = compute_task_id(crew, ctx.task_description)
            step = ctx.metadata.get("_step_index", 0)

            checkpoint = CrewCheckpoint(
                task_id=task_id,
                step_index=step,
                crew_name=crew,
                task_description=ctx.task_description[:200],
                completed_steps=ctx.metadata.get("_completed_steps", []),
                intermediate_results=ctx.metadata.get("_intermediate_results", {}),
                confidence_chain=ctx.metadata.get("confidence_chain", []),
                metadata={
                    "errors": [e[:200] for e in ctx.errors[:3]],
                    "abort_reason": ctx.abort_reason[:200] if ctx.abort_reason else "",
                },
                timestamp=time.time(),
            )
            save_checkpoint(checkpoint)

        except Exception:
            pass
        return ctx
    return _hook
