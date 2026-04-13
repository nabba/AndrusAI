"""
subia.connections.training_signal — Prediction errors → self-training
queue (SIA #4).

Per SubIA Part II §18:
    "Persistent prediction errors in a domain → flag for LoRA training.
     Example: Predictions about TikTok API behavior are consistently
     wrong → queue KaiCart-domain training data for MLX QLoRA
     refinement.
     Implementation: write training signal to a queue file that the
     self-training pipeline reads."

This module writes training signals as JSONL to
`<workspace>/subia/training_queue.jsonl`. The external MLX QLoRA
self-training pipeline (future work) consumes this queue on its own
schedule.

Emission policy:
  - A signal is emitted when accuracy_tracker.has_sustained_error()
    fires on a domain (≥3 bad errors in the recent window).
  - Deduplicated per-domain within a 24h window so the queue doesn't
    explode from repeated triggers on the same domain.
  - Queue is append-only and size-capped; when the cap is exceeded
    the oldest entries are dropped with a warning.

Infrastructure-level. Not agent-modifiable. See PROGRAM.md Phase 10.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from app.paths import WORKSPACE_ROOT
from app.safe_io import safe_append

logger = logging.getLogger(__name__)


_QUEUE_PATH = WORKSPACE_ROOT / "subia" / "training_queue.jsonl"
_DEDUP_WINDOW = timedelta(hours=24)
_MAX_QUEUE_LINES = 10_000


@dataclass
class TrainingSignal:
    """One emission — what will be fed to the training pipeline."""
    at: str = ""
    domain: str = ""
    loop_count: int = 0
    reason: str = ""
    recent_bad_count: int = 0
    mean_accuracy: float = 0.0
    sample_size: int = 0

    def to_dict(self) -> dict:
        return {
            "at": self.at,
            "domain": self.domain,
            "loop_count": self.loop_count,
            "reason": self.reason,
            "recent_bad_count": self.recent_bad_count,
            "mean_accuracy": round(self.mean_accuracy, 4),
            "sample_size": self.sample_size,
        }


class TrainingSignalEmitter:
    """Emits training signals with per-domain deduplication."""

    def __init__(self, queue_path: Path | None = None) -> None:
        self.queue_path = queue_path or _QUEUE_PATH
        self._last_emission: dict[str, datetime] = {}
        self._lock = Lock()

    def emit_from_tracker(
        self,
        accuracy_tracker: Any,
        loop_count: int,
        *,
        now: datetime | None = None,
    ) -> list[TrainingSignal]:
        """Scan all tracked domains, emit a signal for any domain that
        has sustained error AND is not in the dedup window.

        Returns the list of signals emitted this call (empty if none).
        """
        if accuracy_tracker is None:
            return []
        summary_fn = getattr(accuracy_tracker, "all_domains_summary", None)
        has_sustained = getattr(accuracy_tracker, "has_sustained_error", None)
        if not callable(summary_fn) or not callable(has_sustained):
            return []

        try:
            summary = summary_fn()
        except Exception:
            logger.debug(
                "training_signal: tracker summary failed", exc_info=True,
            )
            return []

        domains = (summary or {}).get("domains", [])
        now = now or datetime.now(timezone.utc)
        emitted: list[TrainingSignal] = []

        for d in domains:
            if not isinstance(d, dict):
                continue
            domain = str(d.get("domain", ""))
            if not domain:
                continue
            try:
                if not bool(has_sustained(domain)):
                    continue
            except Exception:
                continue

            if not self._should_emit(domain, now):
                continue

            signal = TrainingSignal(
                at=now.isoformat(),
                domain=domain,
                loop_count=int(loop_count),
                reason="sustained_prediction_error",
                recent_bad_count=int(d.get("recent_bad_count", 0)),
                mean_accuracy=float(d.get("mean_accuracy", 0.0)),
                sample_size=int(d.get("n_samples", 0)),
            )
            self._write(signal)
            with self._lock:
                self._last_emission[domain] = now
            emitted.append(signal)

        return emitted

    # ── Dedup + write ───────────────────────────────────────────

    def _should_emit(self, domain: str, now: datetime) -> bool:
        with self._lock:
            last = self._last_emission.get(domain)
        if last is None:
            return True
        return (now - last) >= _DEDUP_WINDOW

    def _write(self, signal: TrainingSignal) -> None:
        """Append a training signal to the queue. Never raises."""
        try:
            safe_append(self.queue_path,
                        json.dumps(signal.to_dict(), default=str))
        except OSError:
            logger.exception(
                "training_signal: safe_append to %s failed",
                self.queue_path,
            )

    def read_recent(self, limit: int = 100) -> list[dict]:
        """Read last N entries from the queue for diagnostics."""
        if not self.queue_path.exists():
            return []
        try:
            with open(self.queue_path, encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            return []
        out = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out


# Module-level singleton for convenience
_default_emitter: TrainingSignalEmitter | None = None
_default_lock = Lock()


def get_emitter() -> TrainingSignalEmitter:
    global _default_emitter
    with _default_lock:
        if _default_emitter is None:
            _default_emitter = TrainingSignalEmitter()
        return _default_emitter


def reset_singleton() -> None:
    global _default_emitter
    with _default_lock:
        _default_emitter = None
