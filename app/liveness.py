"""Process-liveness heartbeat — decouples watchdog liveness from the event loop.

The gateway serves ``/health`` on the same single asyncio event loop that runs
everything else. Heavy background work (idle ``evolution``/research jobs on the
idle-scheduler daemon thread — LLM token processing, ChromaDB embedding,
``ast.parse``, or a multi-minute evolver ``docker /wait``) can starve that loop,
so ``/health`` answers slower than the host watchdog's probe timeout. The
watchdog could not tell "process alive, loop busy" from "process dead/wedged",
so it restarted busy-but-healthy gateways at the ~120 s threshold — guillotining
in-flight work and never letting heavy jobs finish (see
``scripts/gateway_watchdog.py`` and the 2026-05-31 restart-loop).

This module runs a tiny daemon **thread** (NOT the event loop) that writes a
heartbeat file every few seconds. The write needs only microseconds of the GIL
per tick, so it stays fresh even when the event loop is fully starved — it
proves *the process is alive and the interpreter is scheduling threads*. The
host watchdog reads this file to gate its restart decision: fresh heartbeat +
slow ``/health`` ⇒ busy loop ⇒ don't restart; stale/missing heartbeat ⇒ process
wedged/dead ⇒ restart.

Additive and failure-isolated: a write failure is swallowed (worst case the
heartbeat goes stale and the watchdog falls back to its /health-threshold
decision — i.e. pre-heartbeat behaviour).
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

try:  # canonical workspace root (bind-mounted, host-readable)
    from app.paths import WORKSPACE_ROOT

    _DEFAULT_PATH = str(Path(WORKSPACE_ROOT) / "healing" / "gateway_liveness")
except Exception:  # pragma: no cover - defensive
    _DEFAULT_PATH = "/app/workspace/healing/gateway_liveness"

_HEARTBEAT_PATH = Path(os.environ.get("GATEWAY_LIVENESS_PATH", _DEFAULT_PATH))
_INTERVAL_S = float(os.environ.get("GATEWAY_LIVENESS_INTERVAL_SECONDS", "5"))

_started = False
_start_lock = threading.Lock()


def _beat_once() -> None:
    """Atomically write the current epoch time to the heartbeat file.

    Atomic (tmp + os.replace) so the watchdog can never read a half-written
    value; mtime is also refreshed as a fallback signal.
    """
    _HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _HEARTBEAT_PATH.with_name(_HEARTBEAT_PATH.name + ".tmp")
    tmp.write_text(str(time.time()))
    os.replace(tmp, _HEARTBEAT_PATH)


def _beat_loop() -> None:
    while True:
        try:
            _beat_once()
        except Exception:
            # Never crash the heartbeat thread; a stale file just degrades the
            # watchdog to its /health-threshold decision.
            pass
        time.sleep(_INTERVAL_S)


def start_liveness_heartbeat() -> Path:
    """Start the heartbeat daemon thread once. Idempotent; safe at boot.

    Returns the heartbeat path (for logging). Writes one beat synchronously
    first so the file exists immediately, then hands off to the daemon thread.
    """
    global _started
    with _start_lock:
        if not _started:
            try:
                _beat_once()  # establish the file immediately
            except Exception:
                pass
            threading.Thread(
                target=_beat_loop, name="liveness-heartbeat", daemon=True
            ).start()
            _started = True
    return _HEARTBEAT_PATH
