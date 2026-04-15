"""subia.idle — Phase 12 idle-time job scheduler.

Single registry for all idle-time consciousness work:
  - Reverie cycles (per idle period, ~3-5/period)
  - Understanding passes (queued post-ingest, 1-3/day)
  - Shadow analysis (monthly)

The scheduler does NOT poll. It is invoked explicitly by callers that
already detect idle (e.g. existing `app/idle_scheduler.py` outside
SubIA, or main.py's between-task hook). Each registered job carries
its own throttle policy (min interval, token budget, last-ran).

Design constraint: jobs must be safe to no-op. The scheduler never
raises — failures are logged and the job's last_attempt is bumped so
the throttle still applies.
"""
from .scheduler import (
    IdleJob,
    IdleScheduler,
    get_default_scheduler,
)


def adapt_for_production(job: "IdleJob") -> tuple:
    """Convert a SubIA `IdleJob` into a production idle_scheduler
    `(name, fn, JobWeight)` tuple.

    Phase 16b uses this to unify the two idle-scheduling systems:
    the SubIA `IdleScheduler` (unit-test-grade, token-budget-aware)
    and the production `app.idle_scheduler` (thread-managed, cron-paced,
    Firestore-integrated). SubIA-native job definitions can be written
    against `IdleJob` and then surfaced to the production scheduler via
    this adapter.

    Weight mapping is by interval:
        < 60s      → LIGHT  (monitoring, probes)
        < 300s     → MEDIUM (feedback, safety checks)
        >= 300s    → HEAVY  (evolution, training, retrospective)

    The callable is passed through unchanged — the production
    scheduler wraps it with its own failure tracking, so the SubIA
    `IdleScheduler.tick()` book-keeping becomes redundant for jobs
    registered this way.
    """
    try:
        from app.idle_scheduler import JobWeight
    except Exception:
        # Production scheduler not importable (e.g. in a pure test
        # environment). Return a plain tuple without the weight enum;
        # caller can choose to coerce or skip.
        return (job.name, job.fn, "medium")

    interval = float(getattr(job, "min_interval_seconds", 60.0) or 60.0)
    if interval < 60.0:
        weight = JobWeight.LIGHT
    elif interval < 300.0:
        weight = JobWeight.MEDIUM
    else:
        weight = JobWeight.HEAVY
    return (job.name, job.fn, weight)


__all__ = [
    "IdleJob", "IdleScheduler", "get_default_scheduler",
    "adapt_for_production",
]
