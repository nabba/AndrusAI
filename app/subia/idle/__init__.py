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

__all__ = ["IdleJob", "IdleScheduler", "get_default_scheduler"]
