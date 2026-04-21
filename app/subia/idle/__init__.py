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

Phase 16a follow-up: `production_adapters.py` assembles live adapters
for the three idle engines (Reverie / Understanding / Shadow) using
the existing filesystem / Mem0 / ChromaDB / Neo4j / llm_factory
plumbing. The production idle_scheduler imports the three build_*
factories via the convenience re-exports below.
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


def _build_reverie_engine():
    """Lazy accessor — avoids importing llm_factory / Neo4j / ChromaDB
    at package import time so the idle package stays cheap to import in
    tests and cold environments."""
    from .production_adapters import build_reverie_engine
    return build_reverie_engine()


def _build_understanding_runner():
    from .production_adapters import build_understanding_runner
    return build_understanding_runner()


def _build_shadow_miner(kernel):
    from .production_adapters import build_shadow_miner
    return build_shadow_miner(kernel)


__all__ = [
    "IdleJob", "IdleScheduler", "get_default_scheduler",
    "adapt_for_production",
    "_build_reverie_engine",
    "_build_understanding_runner",
    "_build_shadow_miner",
]
