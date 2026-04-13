"""SubIA idle-time job scheduler.

A small, dependency-light job registry. Each job is throttled by a
minimum interval; the scheduler invokes ready jobs in priority order
and bounds the total token spend per tick.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class IdleJob:
    name: str
    fn: Callable[[], dict]
    min_interval_seconds: float = 60.0
    priority: int = 50              # lower number = higher priority
    token_budget: int = 1_000
    last_ran_at: float = 0.0
    last_succeeded: bool = True
    runs: int = 0
    failures: int = 0

    def is_ready(self, now: float) -> bool:
        return (now - self.last_ran_at) >= self.min_interval_seconds


class IdleScheduler:
    """Process-local job registry. Not thread-safe; the existing CIL
    loop hooks already serialize via the kernel lock."""

    def __init__(self, total_token_budget: int = 10_000) -> None:
        self._jobs: dict[str, IdleJob] = {}
        self._total_token_budget = total_token_budget

    def register(self, job: IdleJob) -> None:
        self._jobs[job.name] = job

    def unregister(self, name: str) -> None:
        self._jobs.pop(name, None)

    def jobs(self) -> list[IdleJob]:
        return list(self._jobs.values())

    def tick(self, *, now: Optional[float] = None) -> dict:
        """Run every ready job in priority order until token budget is
        exhausted. Returns a per-job report dict."""
        now = now if now is not None else time.time()
        report: dict[str, dict] = {}
        spent = 0
        ready = sorted(
            (j for j in self._jobs.values() if j.is_ready(now)),
            key=lambda j: j.priority,
        )
        for job in ready:
            if spent + job.token_budget > self._total_token_budget:
                report[job.name] = {"skipped": "budget"}
                continue
            try:
                result = job.fn() or {}
                job.runs += 1
                job.last_succeeded = True
                spent += int(result.get("tokens_spent", 0)) or job.token_budget
                report[job.name] = {"ok": True, **result}
            except Exception as exc:  # idle work must never crash the caller
                job.failures += 1
                job.last_succeeded = False
                logger.warning("idle job %s failed: %s", job.name, exc)
                report[job.name] = {"ok": False, "error": str(exc)}
            finally:
                job.last_ran_at = now
        return report


_default: IdleScheduler | None = None


def get_default_scheduler() -> IdleScheduler:
    global _default
    if _default is None:
        _default = IdleScheduler()
    return _default
