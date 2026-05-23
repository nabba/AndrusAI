"""F2 — Trial scheduler daemon.

PROGRAM §63 follow-up. Consumes the ``_pending.jsonl`` queue that the
orchestrator writes when a MAJOR finding lacks a cached trial result,
runs at most one trial per scheduler tick, persists the result via
:func:`app.upgrade_lifecycle.orchestrator.persist_trial`, and removes
the row from the pending queue. Idempotent + crash-safe: duplicate
queue rows for the same ``(package, to_version)`` collapse on the
already-running-or-just-finished trial.

Why a separate daemon vs running trials inline in the radar
=============================================================

A pytest run with pip install ahead of it takes 5–15 minutes. The
dependency-radar daemon ticks weekly. If it ran trials inline, the
weekly tick would be slow + the SAME pass would re-attempt the
same trial on every weekly tick because the result wouldn't have
landed before the radar tried the gate again. The scheduler runs
on its own slower cadence + writes results that ALL the next-pass
gates can consume.

Cadence + caps
==============

* **Cadence**: 1 hour per tick. Tunable via ``TRIAL_SCHEDULER_CADENCE_S``.
* **Per-tick cap**: 1 trial. Pytest is expensive; we'd rather have
  the daemon process the backlog gradually than burn a CI-machine's
  worth of CPU on the gateway.
* **Per-(pkg, ver) cooldown**: 7 days. Same pair won't be retried
  within 7d of the last attempt, so repeated failures don't burn
  budget on the same broken pin.

Failure-isolated: every error is caught and logged + recorded as a
TrialResult with ``status="infrastructure_error"``. The daemon
never raises out of its loop.

Master switch: ``upgrade_lifecycle_trial_enabled`` (same gate U3
honors — so disabling trial subsystem disables the scheduler).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from app.upgrade_lifecycle.protocol import TrialResult

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────


TRIAL_SCHEDULER_CADENCE_S = 3600          # 1 hour
WARMUP_S = 180                             # don't fight boot
PER_PAIR_COOLDOWN_DAYS = 7
DAEMON_THREAD_NAME = "ul-trial-scheduler"

_driver_lock = threading.Lock()
_driver_started = False
_stop_event = threading.Event()


def _enabled() -> bool:
    try:
        from app.runtime_settings import get_upgrade_lifecycle_trial_enabled
        return get_upgrade_lifecycle_trial_enabled()
    except Exception:
        return True


# ── Pending-queue management ─────────────────────────────────────────────


def _trials_dir() -> Path:
    override = os.getenv("UPGRADE_LIFECYCLE_DIR")
    if override:
        return Path(override) / "trials"
    try:
        from app.paths import WORKSPACE_ROOT
        return Path(WORKSPACE_ROOT) / "upgrade_lifecycle" / "trials"
    except Exception:
        return Path("/app/workspace/upgrade_lifecycle/trials")


def _pending_path() -> Path:
    return _trials_dir() / "_pending.jsonl"


def _scheduler_state_path() -> Path:
    return _trials_dir() / "_scheduler_state.json"


def _read_state() -> dict:
    p = _scheduler_state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(state: dict) -> None:
    p = _scheduler_state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
        tmp.replace(p)
    except OSError:
        logger.debug("ul.scheduler: state write failed", exc_info=True)


def _read_pending() -> list[dict]:
    """Read + deduplicate the pending queue.

    Two requests for the same ``(package, to_version)`` collapse to
    the EARLIEST. The output is sorted by ``requested_at`` so the
    operator's first request is processed first.
    """
    p = _pending_path()
    if not p.exists():
        return []
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = (str(row.get("package", "")), str(row.get("to_version", "")))
                if key in seen:
                    continue
                seen.add(key)
                out.append(row)
    except OSError:
        return []
    out.sort(key=lambda r: str(r.get("requested_at", "")))
    return out


def _rewrite_pending(rows: list[dict]) -> None:
    """Atomic rewrite of the pending file with *rows*."""
    p = _pending_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, sort_keys=True) + "\n")
        tmp.replace(p)
    except OSError:
        logger.debug("ul.scheduler: pending rewrite failed", exc_info=True)


# ── Cooldown ─────────────────────────────────────────────────────────────


def _cooldown_key(package: str, to_version: str) -> str:
    safe_pkg = package.lower().replace("/", "_")
    safe_ver = to_version.replace("/", "_")
    return f"{safe_pkg}__{safe_ver}"


def _in_cooldown(state: dict, package: str, to_version: str,
                *, now: datetime) -> bool:
    """True iff the pair was attempted in the last 7 days."""
    cooldown_map = state.get("cooldown") or {}
    key = _cooldown_key(package, to_version)
    last_attempt_iso = cooldown_map.get(key)
    if not last_attempt_iso:
        return False
    cutoff = (now - timedelta(days=PER_PAIR_COOLDOWN_DAYS)).isoformat()
    return str(last_attempt_iso) > cutoff


def _record_attempt(state: dict, package: str, to_version: str,
                   *, now: datetime) -> None:
    cooldown_map = dict(state.get("cooldown") or {})
    cooldown_map[_cooldown_key(package, to_version)] = now.isoformat()
    # Prune old entries to keep the state file small.
    cutoff = (now - timedelta(days=PER_PAIR_COOLDOWN_DAYS * 2)).isoformat()
    cooldown_map = {
        k: v for k, v in cooldown_map.items() if str(v) > cutoff
    }
    state["cooldown"] = cooldown_map


# ── Trial execution ──────────────────────────────────────────────────────


def _repo_root() -> Path:
    """Best-effort repo root discovery."""
    try:
        return Path(__file__).resolve().parents[2]
    except Exception:
        return Path.cwd()


def run_one_tick(
    *,
    now: Optional[datetime] = None,
    runner: Optional[Callable] = None,
    repo_root_override: Optional[Path] = None,
) -> dict:
    """Process at most one pending trial. Returns a summary dict.

    Returns: ``{processed: bool, reason: str, package: Optional[str],
              to_version: Optional[str], status: Optional[str]}``.

    Injectable ``runner`` for tests — defaults to
    :func:`app.upgrade_lifecycle.trial_runner.run_trial`.
    """
    now_dt = now or datetime.now(timezone.utc)
    summary: dict = {
        "processed": False, "reason": "",
        "package": None, "to_version": None, "status": None,
    }

    if not _enabled():
        summary["reason"] = "master_switch_off"
        return summary

    pending = _read_pending()
    if not pending:
        summary["reason"] = "no_pending"
        return summary

    state = _read_state()

    # Find the first pending entry NOT in cooldown.
    target: Optional[dict] = None
    skipped_cooldown_rows: list[dict] = []
    for row in pending:
        pkg = str(row.get("package", ""))
        ver = str(row.get("to_version", ""))
        if not pkg or not ver:
            continue
        if _in_cooldown(state, pkg, ver, now=now_dt):
            skipped_cooldown_rows.append(row)
            continue
        target = row
        break

    if target is None:
        summary["reason"] = "all_in_cooldown"
        return summary

    pkg = str(target["package"])
    ver = str(target["to_version"])
    from_ver = str(target.get("from_version") or "unknown")

    summary["package"] = pkg
    summary["to_version"] = ver

    # Mark attempt BEFORE running so a crash mid-run still respects cooldown.
    _record_attempt(state, pkg, ver, now=now_dt)
    state["last_tick_at"] = now_dt.isoformat()
    _write_state(state)

    # Run the trial.
    if runner is None:
        from app.upgrade_lifecycle.trial_runner import run_trial
        runner = run_trial

    repo_root = repo_root_override or _repo_root()
    try:
        result: TrialResult = runner(
            package=pkg, from_version=from_ver, to_version=ver,
            repo_root=repo_root,
        )
    except Exception as exc:
        logger.debug("ul.scheduler: trial runner raised", exc_info=True)
        result = TrialResult(
            package=pkg, from_version=from_ver, to_version=ver,
            status="infrastructure_error",
            failures=(str(exc)[:200],),
        )

    # Persist result.
    try:
        from app.upgrade_lifecycle.orchestrator import persist_trial
        persist_trial(result)
    except Exception:
        logger.debug("ul.scheduler: persist_trial failed", exc_info=True)

    # Remove this entry from the pending queue.
    new_pending = [
        r for r in pending
        if not (str(r.get("package", "")) == pkg
                and str(r.get("to_version", "")) == ver)
    ]
    _rewrite_pending(new_pending)

    summary["processed"] = True
    summary["reason"] = "ok"
    summary["status"] = result.status
    return summary


# ── Daemon driver ────────────────────────────────────────────────────────


def _driver() -> None:
    """Tight loop — waits cadence, runs one tick, repeats."""
    if _stop_event.wait(WARMUP_S):
        return
    while not _stop_event.is_set():
        try:
            run_one_tick()
        except Exception:
            logger.debug("ul.scheduler: tick failed", exc_info=True)
        if _stop_event.wait(TRIAL_SCHEDULER_CADENCE_S):
            return


def _thread_alive() -> bool:
    """A1-P0 — true iff a daemon thread by our canonical name is alive.

    Replaces the previous sticky-flag check. The watchdog's respawn
    contract requires this — when a daemon dies, the watchdog needs
    ``start()`` to actually start a new thread, not refuse because a
    stale flag says one's running.
    """
    return any(
        t.name == DAEMON_THREAD_NAME and t.is_alive()
        for t in threading.enumerate()
    )


def start() -> bool:
    """Start the daemon thread. Returns True if it started; False if
    already running or disabled.

    Thread-liveness-aware per the ``healing.watchdog`` contract: re-
    entry while an existing thread is ALIVE is a no-op; re-entry
    after the thread has died WILL re-spawn it. This is what makes
    the watchdog's 60s respawn loop actually work.
    """
    global _driver_started
    if not _enabled():
        return False
    with _driver_lock:
        if _thread_alive():
            return False
        # Reset stop event in case it was set by a previous incarnation
        # whose thread is now dead — we want the new thread to run.
        if _stop_event.is_set():
            _stop_event.clear()
        thread = threading.Thread(
            target=_driver, name=DAEMON_THREAD_NAME, daemon=True,
        )
        thread.start()
        _driver_started = True
    logger.info("ul.scheduler: trial scheduler daemon started")
    return True


def stop() -> None:
    """Signal the daemon to exit. Best-effort — daemon threads die
    with the process anyway."""
    _stop_event.set()


def is_running() -> bool:
    """True iff the daemon thread is currently alive."""
    return _thread_alive()
