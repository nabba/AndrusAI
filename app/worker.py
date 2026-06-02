"""Standalone idle-scheduler worker — serving/compute split (2026-06-01).

Runs the idle scheduler in a SEPARATE process from the gateway so heavy
background jobs (evolution, research, etc.) don't starve the gateway's asyncio
event loop / ``/health``. Started by the ``worker`` docker-compose service with
``IDLE_SCHEDULER_ROLE=worker`` — default-OFF (opt-in via
``docker compose --profile worker up -d worker``).

SAFETY: this process must NEVER open ChromaDB (embedded is single-writer; a
second writer corrupts the KBs — §55). ``app.memory.chromadb_manager``
fail-closed-guards every client open when ``IDLE_SCHEDULER_ROLE=worker``, so a
misclassified job raises loudly instead of corrupting. Worker-eligible jobs are
an explicit allowlist in ``idle_scheduler._WORKER_ELIGIBLE_JOBS`` (empty until
each job is verified chromadb-free or converted to ledger-first writes).

NOTE (follow-up): importing ``app.idle_scheduler`` pulls in subsystems that may
eager-wire gateway daemons (e.g. ``app.healing``). Auditing/trimming those so
the worker runs ONLY the idle loop (no duplicate gateway daemons) is part of
Phase 2 — keep the eligible-jobs allowlist small until then.

Run: ``python -m app.worker``
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import time

# Set BEFORE importing app modules so the chromadb fail-closed guard + the
# scheduler job filter both see worker mode.
os.environ.setdefault("IDLE_SCHEDULER_ROLE", "worker")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("idle_worker")

_stop = {"flag": False}


def _handle_signal(signum, _frame) -> None:
    log.info("idle worker received signal %s — stopping", signum)
    _stop["flag"] = True


def main() -> int:
    role = os.environ.get("IDLE_SCHEDULER_ROLE")
    if role != "worker":
        log.error("app.worker requires IDLE_SCHEDULER_ROLE=worker (got %r)", role)
        return 2
    log.info("idle worker starting (role=worker)")

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    from app import boot_state, idle_scheduler

    # No FastAPI lifespan here, so signal boot-complete ourselves — otherwise
    # the scheduler's boot-fallback would needlessly delay job execution.
    try:
        boot_state.mark_boot_complete()
    except Exception:
        log.warning("boot_state.mark_boot_complete failed (non-fatal)", exc_info=True)

    # start() filters to worker-eligible jobs via IDLE_SCHEDULER_ROLE.
    idle_scheduler.start()

    while not _stop["flag"]:
        time.sleep(1)

    try:
        idle_scheduler.stop()
    except Exception:
        log.warning("idle_scheduler.stop failed (non-fatal)", exc_info=True)
    log.info("idle worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
