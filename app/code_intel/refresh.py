"""Idle-job refresh for the code-intel index (Phase 3 piece 1b, 2026-05-20).

One scheduler tick = one rebuild of the symbol index if the cadence
guard says it's time. The refresh is master-switch-gated
(``code_intel_enabled`` in runtime_settings); when off, this function
is a microsecond no-op.

Why a periodic rebuild rather than incremental:
  * v1 is pure-Python AST + JSONL. Full rebuild on this repo takes
    seconds (a few hundred files). Incremental indexing would add
    significant complexity for very little wall-clock win at v1
    scale.
  * Future v2 with pyright/tree-sitter sidecar will switch to
    file-watcher-driven incremental updates; the public ``refresh()``
    surface stays the same.

The refresh:
  1. Bails immediately if the master switch is off.
  2. Bails immediately if the last refresh was within the cadence
     window (default 30 minutes).
  3. Calls ``build_index(root=app_root)`` + ``save_index(snap)``.
  4. Records the timestamp in a state JSON so the next tick can
     decide whether to fire.
  5. Logs the stats summary for operator visibility.

Failure-isolated: any exception during step 3 is logged + counted in
state for diagnostics; the next tick retries.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# Default cadence: 30 minutes. Operators tighten via the env var
# CODE_INTEL_CADENCE_S — useful in dev where you want fresher index.
_DEFAULT_CADENCE_S = 30 * 60

# Refresh state file lives next to the index data.
_STATE_FILENAME = "refresh_state.json"


_state_lock = threading.RLock()


def _cadence_s() -> int:
    """Read the cadence override from env. Falls back to default."""
    import os
    raw = os.environ.get("CODE_INTEL_CADENCE_S", "").strip()
    if not raw:
        return _DEFAULT_CADENCE_S
    try:
        v = int(raw)
        if v <= 0:
            return _DEFAULT_CADENCE_S
        return v
    except (TypeError, ValueError):
        return _DEFAULT_CADENCE_S


def _is_enabled() -> bool:
    """Read the master switch. Defensive: any failure → False."""
    try:
        from app.runtime_settings import get_code_intel_enabled
        return get_code_intel_enabled()
    except Exception:
        return False


def _state_path() -> Path:
    """Where the refresh-cadence state JSON lives."""
    from app.code_intel.store import get_base_dir
    return get_base_dir() / _STATE_FILENAME


def _read_state() -> dict:
    """Return the current refresh-state dict; defensive on missing /
    malformed."""
    path = _state_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(state: dict) -> None:
    """Atomic state write. Best-effort — failure logs only."""
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        logger.warning(
            "code_intel.refresh: state write failed: %s", exc,
        )


def _now_ts() -> float:
    return time.time()


def _resolve_app_root() -> Path:
    """Where the index lives. Production: ``/app``. Local dev: the
    repo root. Honour the ``CODE_INTEL_ROOT`` env override for tests
    + special cases."""
    import os
    override = os.environ.get("CODE_INTEL_ROOT", "").strip()
    if override:
        return Path(override).resolve()
    return Path("/app")


def run_refresh(*, force: bool = False) -> dict:
    """One refresh cycle. Returns a summary dict for operator visibility.

    Parameters
    ----------
    force
        Bypass the cadence guard. Useful for tests + operator-initiated
        rebuilds.

    Returns
    -------
    dict
        ``{"ran": bool, "skipped_reason": str, "stats": dict,
           "elapsed_s": float, "error": str}``.

    Never raises. All failures land in the ``error`` field.
    """
    if not _is_enabled() and not force:
        return {
            "ran": False,
            "skipped_reason": "master_switch_off",
            "stats": {},
            "elapsed_s": 0.0,
            "error": "",
        }

    with _state_lock:
        state = _read_state()
        cadence = _cadence_s()
        last_at = float(state.get("last_refresh_at", 0))
        now = _now_ts()
        if not force and (now - last_at) < cadence:
            return {
                "ran": False,
                "skipped_reason": (
                    f"cadence_guard: {int(now - last_at)}s since last "
                    f"refresh < {cadence}s window"
                ),
                "stats": state.get("last_stats", {}),
                "elapsed_s": 0.0,
                "error": "",
            }

        # Reserve our slot before doing the work — guards against
        # accidental concurrent refresh in dev environments.
        state["last_refresh_at"] = now
        _write_state(state)

    started = time.monotonic()
    try:
        from app.code_intel.indexer import build_index
        from app.code_intel.store import save_index
        root = _resolve_app_root()
        if not root.exists() or not root.is_dir():
            error_msg = f"app_root not found: {root}"
            logger.warning("code_intel.refresh: %s", error_msg)
            with _state_lock:
                state["last_error"] = error_msg
                _write_state(state)
            return {
                "ran": False, "skipped_reason": "root_missing",
                "stats": {}, "elapsed_s": time.monotonic() - started,
                "error": error_msg,
            }
        snapshot = build_index(root=root)
        stats = save_index(snapshot)
    except Exception as exc:
        elapsed = time.monotonic() - started
        logger.exception(
            "code_intel.refresh: refresh failed after %.2fs",
            elapsed,
        )
        with _state_lock:
            state["last_error"] = f"{type(exc).__name__}: {exc}"
            state["last_failed_at"] = _now_ts()
            _write_state(state)
        return {
            "ran": False, "skipped_reason": "exception",
            "stats": {}, "elapsed_s": elapsed,
            "error": f"{type(exc).__name__}: {exc}",
        }

    elapsed = time.monotonic() - started
    with _state_lock:
        state["last_stats"] = stats
        state["last_elapsed_s"] = elapsed
        state["last_success_at"] = _now_ts()
        state.pop("last_error", None)
        state["last_indexed_at_iso"] = datetime.now(
            timezone.utc,
        ).isoformat()
        _write_state(state)

    logger.info(
        "code_intel.refresh: indexed %d symbols, %d references "
        "across %d files in %.2fs",
        stats.get("symbols", 0),
        stats.get("references", 0),
        stats.get("indexed_files", 0),
        elapsed,
    )
    return {
        "ran": True, "skipped_reason": "",
        "stats": stats, "elapsed_s": elapsed,
        "error": "",
    }


def reset_state_for_tests() -> None:
    """Test helper — clear the cadence state so the next refresh fires."""
    path = _state_path()
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass
