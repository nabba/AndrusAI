"""Telemetry for PhenomenalLanguageLinter rejections in thread-closure
distillation.

Background — 2026-05-23 Round 2 audit follow-up. ``_llm_distill`` in
``app/threads/approaches.py`` was given a HARD_FAIL post-filter:
when the LLM-distilled "approaches tried" summary contains a first-
person phenomenal claim (e.g. "I feel that..." — though that specific
form is exempted by the linter; "I am curious" would trip it), the
function returns "" so the caller falls back to the deterministic
body builder.

That fallback was silent. Logging at DEBUG only. The whole point of
the SubIA audit was identifying silent-failure patterns; not surfacing
THIS one would re-introduce the same antipattern.

This module gives the rejection an operator-visible footprint:

  * ``record_rejection(...)`` appends one row per HARD_FAIL to
    ``workspace/threads/linter_rejections.jsonl`` (capped via
    ``append_with_cap`` at 1000 rows) and updates a running summary
    at ``workspace/threads/linter_state.json``.
  * ``summary()`` reads the summary file; operator-facing surfaces
    (a future briefing section, REST endpoint, CLI) call this.

Both are failure-isolated end-to-end — a broken telemetry write
NEVER blocks the distill caller's fallback path.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_MAX_ROWS = 1000

# Module-level lock — guards both the JSONL append and the
# summary-file read-modify-write.
_LOCK = threading.Lock()


def _workspace_root() -> Path:
    """Honor WORKSPACE_ROOT override (matches the pattern used by
    other workspace-writers like life_companion)."""
    return Path(os.getenv("WORKSPACE_ROOT", "/app/workspace"))


def _rejection_log_path() -> Path:
    return _workspace_root() / "threads" / "linter_rejections.jsonl"


def _state_path() -> Path:
    return _workspace_root() / "threads" / "linter_state.json"


@dataclass(frozen=True)
class LinterRejection:
    ts: str
    thread_id: str
    violation_count: int
    sample_pattern: str
    body_text_len: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "thread_id": self.thread_id,
            "violation_count": self.violation_count,
            "sample_pattern": self.sample_pattern,
            "body_text_len": self.body_text_len,
        }


def record_rejection(
    *,
    thread_id: str,
    violations: list,  # list[PhenomenalViolation] from the linter
    body_text_len: int,
) -> bool:
    """Append a rejection row + bump the running summary.

    Returns True on success, False on any failure. Caller treats False
    as "telemetry unavailable" — never blocks the distill fallback."""
    try:
        sample_pattern = ""
        if violations:
            sample = violations[0]
            sample_pattern = getattr(sample, "explanation", "") or getattr(
                sample, "pattern", ""
            )
        row = LinterRejection(
            ts=datetime.now(timezone.utc).isoformat(),
            thread_id=str(thread_id),
            violation_count=len(violations or []),
            sample_pattern=str(sample_pattern)[:120],
            body_text_len=int(body_text_len),
        )
    except Exception:
        logger.debug("linter_telemetry: failed to build row", exc_info=True)
        return False

    path = _rejection_log_path()
    state_path = _state_path()

    with _LOCK:
        # Append row to JSONL (capped).
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            from app.utils.jsonl_retention import append_with_cap
            append_with_cap(path, json.dumps(row.to_dict()), max_lines=_MAX_ROWS)
        except Exception:
            logger.debug(
                "linter_telemetry: rejection-log append failed",
                exc_info=True,
            )
            # State file is the cheap recoverable surface — try to bump
            # it even if the log append failed.

        # Update summary state (last_rejection_ts, total_rejections,
        # by_pattern Counter). Read-modify-write under the lock.
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                cur = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cur = {}
            cur["last_rejection_ts"] = row.ts
            cur["total_rejections"] = int(cur.get("total_rejections", 0)) + 1
            by_pattern = dict(cur.get("by_pattern", {}))
            by_pattern[row.sample_pattern] = (
                int(by_pattern.get(row.sample_pattern, 0)) + 1
            )
            cur["by_pattern"] = by_pattern
            tmp = state_path.with_suffix(state_path.suffix + ".tmp")
            tmp.write_text(json.dumps(cur, sort_keys=True), encoding="utf-8")
            tmp.replace(state_path)
        except Exception:
            logger.debug(
                "linter_telemetry: state-file update failed",
                exc_info=True,
            )
            return False
    return True


def summary() -> dict[str, Any]:
    """Read the running summary. Returns an empty-but-shaped dict if
    no rejections have been recorded yet (so callers don't have to
    handle None)."""
    path = _state_path()
    if not path.exists():
        return {
            "last_rejection_ts": None,
            "total_rejections": 0,
            "by_pattern": {},
        }
    try:
        cur = json.loads(path.read_text(encoding="utf-8"))
        return {
            "last_rejection_ts": cur.get("last_rejection_ts"),
            "total_rejections": int(cur.get("total_rejections", 0)),
            "by_pattern": dict(cur.get("by_pattern", {})),
        }
    except (OSError, json.JSONDecodeError):
        return {
            "last_rejection_ts": None,
            "total_rejections": 0,
            "by_pattern": {},
        }
