"""Boot-burst event-loop responsiveness probe.

The gateway's ``/health`` route is a one-line ``async def`` returning a
static dict — its round-trip latency is therefore a direct measure of
asyncio event-loop responsiveness, untainted by DB or disk work. This
daemon probes ``/health`` on a fixed cadence and records observations
whose round-trip exceeds a slow threshold (or fails outright) to a
JSONL ledger.

The intent is forensic, not corrective: when the host_substrate watchdog
restarts the gateway (~120 s of unresponsive ``/health`` probes), the
ledger lets us correlate the stall window against other logs to identify
*which* concurrently-running job class blocked the loop. The probe runs
purely against the local interface, costs ~one HTTP round-trip per
:data:`_PROBE_INTERVAL_S`, and emits zero output when responsiveness is
healthy — the warm-up window is the only chatter.

Boot-anchored via :mod:`app.healing`. Disable via
``BOOT_DIAGNOSTICS_ENABLED=false``.

Composes with — does not replace — the existing
``signal_heartbeat`` monitor (which watches signal-cli liveness) and
the host-side ``gateway_watchdog`` (which restarts on stall). Those
are remediators; this is the observability source they don't yet have.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)


_WARMUP_S = 30                         # let lifespan finish before first probe
_PROBE_INTERVAL_S = 10                 # cadence between probes
_SLOW_THRESHOLD_S = 1.0                # log + record above this latency
_PROBE_TIMEOUT_S = 30                  # HTTP timeout per probe
_LEDGER_NAME = "event_loop_latency.jsonl"
_LEDGER_LINE_CAP = 10_000              # rotate-by-cap to keep ledger bounded
_THREAD_NAME = "boot-diagnostics-probe"


_stop_event = threading.Event()


def _enabled() -> bool:
    """Master switch. Default ON — the probe is cheap and forensic data
    is most valuable at the moment of a hang, not after."""
    return os.environ.get("BOOT_DIAGNOSTICS_ENABLED", "true").lower() in (
        "1", "true", "yes", "on",
    )


def _gateway_url() -> str:
    """Local URL of the gateway. Defaults to ``127.0.0.1:8765`` — same
    interface the host watchdog probes. Override via env so a
    differently-bound gateway can still self-probe."""
    return os.environ.get("BOOT_DIAGNOSTICS_HEALTH_URL", "http://127.0.0.1:8765/health")


def _ledger_path() -> Path:
    try:
        from app.paths import WORKSPACE_ROOT
        return Path(WORKSPACE_ROOT) / "observability" / _LEDGER_NAME
    except Exception:
        return Path("/app/workspace/observability") / _LEDGER_NAME


def _append_observation(row: dict) -> None:
    """Append-only ledger write with cap-based rotation. Failure-isolated."""
    path = _ledger_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError:
        logger.debug("boot_diagnostics: ledger append failed", exc_info=True)
        return

    # Lightweight rotation when the file crosses the cap. Rebuilds in
    # one pass — never grows the file more than ~2× the cap before
    # collapsing back. No external dependency.
    try:
        with path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > _LEDGER_LINE_CAP * 2:
            keep = lines[-_LEDGER_LINE_CAP:]
            tmp = path.with_suffix(".jsonl.tmp")
            tmp.write_text("".join(keep), encoding="utf-8")
            tmp.replace(path)
    except OSError:
        pass


def _probe_once() -> None:
    """One round-trip against /health. Records on slow or failure."""
    import requests  # imported lazily; gateway image always has it

    url = _gateway_url()
    started_wall = time.time()
    t0 = time.monotonic()
    try:
        resp = requests.get(url, timeout=_PROBE_TIMEOUT_S)
        elapsed = time.monotonic() - t0
        ok = resp.status_code == 200
        if elapsed >= _SLOW_THRESHOLD_S or not ok:
            logger.warning(
                "boot_diagnostics: /health probe %.2fs status=%d — "
                "event-loop responsiveness degraded",
                elapsed, resp.status_code,
            )
            _append_observation({
                "ts": started_wall,
                "elapsed_s": round(elapsed, 3),
                "status": resp.status_code,
                "ok": ok,
            })
    except requests.exceptions.RequestException as exc:
        # The hang case — /health itself failed to respond. This is the
        # signal the host watchdog acts on; we log it from the inside so
        # there's a record of WHEN the stall began (the watchdog only
        # sees the 120 s aggregate).
        elapsed = time.monotonic() - t0
        logger.warning(
            "boot_diagnostics: /health probe failed after %.2fs (%s) — "
            "event loop unresponsive",
            elapsed, type(exc).__name__,
        )
        _append_observation({
            "ts": started_wall,
            "elapsed_s": round(elapsed, 3),
            "status": None,
            "ok": False,
            "error": type(exc).__name__,
        })


def _probe_loop() -> None:
    """Daemon main loop."""
    if _stop_event.wait(_WARMUP_S):
        return
    while not _stop_event.is_set():
        try:
            _probe_once()
        except Exception:
            # Last-resort guard so the daemon never dies on an unexpected
            # internal error. The probe itself is already failure-isolated;
            # this catches anything escaping that.
            logger.debug("boot_diagnostics: unexpected probe error", exc_info=True)
        if _stop_event.wait(_PROBE_INTERVAL_S):
            return


def _is_running() -> bool:
    return any(
        t.name == _THREAD_NAME and t.is_alive()
        for t in threading.enumerate()
    )


def start() -> None:
    """Idempotent daemon start. Re-callable by the healing watchdog
    when the probe thread dies — only spawns if no thread by name
    is currently alive."""
    if not _enabled():
        logger.info("boot_diagnostics: disabled via BOOT_DIAGNOSTICS_ENABLED")
        return
    if _is_running():
        return
    thread = threading.Thread(
        target=_probe_loop, name=_THREAD_NAME, daemon=True,
    )
    thread.start()
    logger.info(
        "boot_diagnostics: probe daemon started "
        "(interval=%ds, slow_threshold=%.1fs, ledger=%s)",
        _PROBE_INTERVAL_S, _SLOW_THRESHOLD_S, _ledger_path(),
    )


def stop() -> None:
    """Signal the probe loop to exit. Used by tests."""
    _stop_event.set()


# Eager-start on import — matches the pattern of every other
# observational daemon anchored from app.healing.__init__.
start()
