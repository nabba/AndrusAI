"""Boot-burst event-loop responsiveness probe.

The gateway's ``/health`` route is a one-line ``async def`` returning a
static dict — its round-trip latency is therefore a direct measure of
asyncio event-loop responsiveness, untainted by DB or disk work. This
daemon probes ``/health`` on a fixed cadence and records observations
whose round-trip exceeds a slow threshold (or fails outright).

The intent is forensic, not corrective: when the host gateway-watchdog
restarts the gateway (~120 s of unresponsive ``/health`` probes), the
observation record lets us correlate the stall window against other
logs to identify *which* concurrently-running job class blocked the
loop. The probe runs purely against the local interface, costs ~one
HTTP round-trip per :data:`_PROBE_INTERVAL_S`, and emits zero output
when responsiveness is healthy — the warm-up window is the only
chatter.

Two output paths, deliberately layered:

* **Structured WARNING to stderr** (``_emit_observation_warning``) —
  the cross-restart forensic surface. Docker captures stderr at the
  kernel level, so a stalled asyncio loop still gets the record
  through to ``docker compose logs gateway --since=...``. Each
  warning is prefixed ``boot_diagnostics_observation`` followed by
  one canonical JSON line; the operator can ``grep`` + ``jq`` slice.
* **Local JSONL on container tmpfs** at
  ``/tmp/observability/event_loop_latency.jsonl`` — a convenience
  for live querying within the current container life. Deliberately
  NOT on the bind-mounted workspace: that is the very filesystem
  most-likely-congested during the stall this probe exists to
  observe. tmpfs writes are O(memory), never block on disk IO.

The local ledger is wiped on container restart by design; the
structured WARNING is the persistent record. Override the local
path via ``BOOT_DIAGNOSTICS_LEDGER_PATH``; disable the whole
subsystem via ``BOOT_DIAGNOSTICS_ENABLED=false``.

Boot-anchored via :mod:`app.healing`. Composes with — does not
replace — the existing ``signal_heartbeat`` monitor (signal-cli
liveness) and the host-side ``gateway_watchdog`` (restarts on
stall). Those are remediators; this is the observability source
they don't yet have.
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
    """Live ledger lives on container-local tmpfs.

    Two failure modes drove this choice. (1) The bind-mounted
    ``/app/workspace`` is the most-likely-congested filesystem when
    the asyncio loop is stalled — writing the FORENSIC record there
    would block the diagnostic daemon on the same disk-IO event it is
    trying to observe. (2) Container-local writes are O(memory),
    never touch the slow path. Tradeoff: tmpfs is wiped on container
    restart, so the live ledger covers only the current process life.
    The structured WARNING emitted in parallel (see
    :func:`_emit_observation_warning`) is the cross-restart record —
    Docker captures stderr-bound logs at the kernel level, surviving
    container restart, and the host can read them with
    ``docker compose logs --since=...``.
    """
    override = os.environ.get("BOOT_DIAGNOSTICS_LEDGER_PATH")
    if override:
        return Path(override)
    return Path("/tmp/observability") / _LEDGER_NAME


def _emit_observation_warning(row: dict) -> None:
    """Emit a single-line structured WARNING.

    This is the cross-restart forensic surface. Stderr writes from a
    worker thread bypass the asyncio event loop entirely — they hit
    the file descriptor directly, which Docker's logging driver
    captures at the kernel level. So a stalled loop still gets the
    record into ``docker compose logs gateway --since=...`` for
    post-hang correlation.

    Format: ``boot_diagnostics_observation {<json>}`` — one canonical
    prefix lets the operator grep + ``jq`` slice the payload.
    """
    try:
        payload = json.dumps(row, sort_keys=True)
        logger.warning("boot_diagnostics_observation %s", payload)
    except Exception:
        logger.debug("boot_diagnostics: warn emit failed", exc_info=True)


def _append_observation(row: dict) -> None:
    """Local ledger write with cap-based rotation. Failure-isolated.

    Composed in parallel with :func:`_emit_observation_warning` so
    that an OSError or unexpected exception in one path never
    suppresses the other. The structured WARNING is the source of
    truth for cross-restart forensics; this local ledger is a
    convenience for live querying.
    """
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


def _record_observation(row: dict) -> None:
    """Single entry point — emit WARNING + local ledger in that order.

    Order matters: WARNING is the forensic record we MUST not lose,
    so it goes first. The local ledger is best-effort. If the ledger
    write blocks or fails, the WARNING is already in flight.
    """
    _emit_observation_warning(row)
    _append_observation(row)


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
            _record_observation({
                "ts": started_wall,
                "elapsed_s": round(elapsed, 3),
                "status": resp.status_code,
                "ok": ok,
            })
    except requests.exceptions.RequestException as exc:
        # The hang case — /health itself failed to respond. This is
        # the signal the host watchdog acts on; we record it from the
        # inside so there's a per-probe timestamp of WHEN the stall
        # began (the watchdog only sees the 120 s aggregate).
        elapsed = time.monotonic() - t0
        _record_observation({
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
