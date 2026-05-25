"""Idle-job entry points for the upgrade-lifecycle subsystem.

PROGRAM §63 follow-up (F6). Three LIGHT jobs registered in
``app.companion.loop.get_idle_jobs``:

  * ``upgrade-ecosystem-snapshot`` — once per calendar year (January
    cron-equivalent). Calls :func:`generate_snapshot` with
    ``force=False`` so re-firing within the same year is a no-op.
  * ``upgrade-capability-adoption`` — weekly LIGHT pass; tries to
    propose at most one U5 capability-adoption CR per ISO week.
    Internal gates (rate-limit + budget + Goodhart pause) keep the
    daily fire a no-op for the rest of the week.
  * ``upgrade-lifecycle-goodhart`` — weekly re-evaluation of the
    Goodhart throttle state (MAJOR window + adoption pause).

Each entry point is failure-isolated: any exception is logged and
swallowed, the scheduler tick reports success, the daily loop keeps
running.

Daily-fire cadence: the snapshot pass cadence-checks via
``_read_snapshot(year) is not None`` (idempotent re-runs) — equivalent
to the identity-reflection pattern. The capability-adoption job
defers to its own rate-limit + budget gates internally.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable

logger = logging.getLogger(__name__)


# ── Snapshot pass (annual cadence; daily fire is a no-op) ────────────────


# January is the firing window — sticking to the first week keeps the
# logic conservative and avoids races between two daemon restarts on
# the same boundary day.
_SNAPSHOT_FIRE_MONTH = 1
_SNAPSHOT_FIRE_DAY_MAX = 7


def run_annual_snapshot() -> dict:
    """Generate the annual ecosystem snapshot if it's missing for this year.

    Returns a small status dict (consumed by the scheduler for logs only).
    Outside the January firing window: skipped_outside_window.
    Inside the window but snapshot exists: skipped_exists.
    Inside the window with no snapshot: ok with the resulting year.
    """
    now = datetime.now(timezone.utc)
    out: dict = {"status": "ok", "year": now.year}

    if now.month != _SNAPSHOT_FIRE_MONTH or now.day > _SNAPSHOT_FIRE_DAY_MAX:
        out["status"] = "skipped_outside_window"
        return out

    try:
        from app.upgrade_lifecycle.ecosystem_snapshot import (
            _read_snapshot,
            generate_snapshot,
        )
    except Exception:
        logger.debug("ul.idle: ecosystem_snapshot import failed", exc_info=True)
        out["status"] = "import_failed"
        return out

    if _read_snapshot(now.year) is not None:
        out["status"] = "skipped_exists"
        return out

    try:
        snapshot = generate_snapshot(year=now.year, now=now, force=False)
    except Exception:
        logger.debug("ul.idle: generate_snapshot raised", exc_info=True)
        out["status"] = "error"
        return out

    if snapshot is None:
        out["status"] = "skipped_disabled"
    else:
        out["status"] = "ok"
        out["year"] = snapshot.year
        out["major_upgrade_count"] = len(snapshot.major_upgrades)
        logger.info(
            "ul.idle: annual snapshot generated year=%d majors=%d",
            snapshot.year, len(snapshot.major_upgrades),
        )
        _notify_snapshot_ready(snapshot)
    return out


def _notify_snapshot_ready(snapshot) -> None:
    """Fire a Signal + Web Push ping with iPhone/Mac links to /cp/ecosystem.

    Without this, the annual major-upgrade plan lands silently in
    ``wiki/self/ecosystem/<year>.md`` and the operator may not ratify
    until next year's drift causes a separate surface to fire.
    Failure-isolated: a broken notify must not break the idle job.
    """
    try:
        from app.notify import notify
        from app.dashboard_links import signal_links_block

        body_lines = [
            f"Year {snapshot.year}: {len(snapshot.major_upgrades)} "
            f"major upgrades proposed.",
        ]
        days = snapshot.python_eol.get("days_until_eol")
        if isinstance(days, (int, float)) and days < 365:
            body_lines.append(f"Python EOL: {int(days)}d away.")
        body_lines.append("")
        body_lines.append(signal_links_block("/cp/ecosystem"))

        notify(
            title=f"📅 Ecosystem snapshot {snapshot.year}",
            body="\n".join(body_lines),
            url="/cp/ecosystem",
            tag="upgrade_lifecycle",
        )
    except Exception:
        logger.debug("ul.idle: snapshot notify failed", exc_info=True)


# ── Capability-adoption pass (weekly; daily fire defers internally) ─────


def run_capability_adoption() -> dict:
    """Daily tick — invokes :func:`capability_adoption.run_one_pass`.

    The pass has four internal gates (master switch + Goodhart pause +
    1-CR-per-ISO-week + quarterly budget). On most days every gate
    short-circuits and the call returns in <1 ms.
    """
    try:
        from app.upgrade_lifecycle.capability_adoption import run_one_pass
    except Exception:
        logger.debug("ul.idle: capability_adoption import failed", exc_info=True)
        return {"reason": "import_failed"}
    try:
        return run_one_pass()
    except Exception:
        logger.debug("ul.idle: capability_adoption raised", exc_info=True)
        return {"reason": "error"}


# ── Goodhart throttle re-evaluation (weekly; daily fire is cheap) ────────


_THROTTLE_STATE_FILE = ".goodhart_throttle_tick.json"


def _throttle_state_path():
    from pathlib import Path
    import os
    override = os.getenv("UPGRADE_LIFECYCLE_DIR")
    if override:
        return Path(override) / _THROTTLE_STATE_FILE
    try:
        from app.paths import WORKSPACE_ROOT
        return Path(WORKSPACE_ROOT) / "upgrade_lifecycle" / _THROTTLE_STATE_FILE
    except Exception:
        return Path("/app/workspace/upgrade_lifecycle") / _THROTTLE_STATE_FILE


def run_goodhart_throttle() -> dict:
    """Weekly re-evaluation of U9 throttle state.

    Reads the CR audit log + persists the resulting MAJOR window /
    adoption-pause decision. Cheap — a few hundred audit-log lines + a
    counting walk; no LLM, no network. Internal cadence guard: only
    re-evaluates if at least 7 days have passed since the last
    successful evaluation.
    """
    import json
    out: dict = {"status": "ok"}
    now = datetime.now(timezone.utc)
    state_path = _throttle_state_path()
    try:
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            last_iso = state.get("last_run_at")
            if last_iso:
                last = datetime.fromisoformat(str(last_iso))
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if (now - last).total_seconds() < 7 * 24 * 3600:
                    out["status"] = "skipped_recent"
                    return out
    except Exception:
        pass   # corrupt state — proceed as if first run

    try:
        from app.upgrade_lifecycle.goodhart import (
            evaluate_adoption_pause,
            evaluate_major_window,
        )
    except Exception:
        logger.debug("ul.idle: goodhart import failed", exc_info=True)
        out["status"] = "import_failed"
        return out

    try:
        out["major_window_days"] = evaluate_major_window(now=now)
    except Exception:
        logger.debug("ul.idle: evaluate_major_window raised", exc_info=True)
    try:
        out["adoption_paused_until"] = evaluate_adoption_pause(now=now)
    except Exception:
        logger.debug("ul.idle: evaluate_adoption_pause raised", exc_info=True)

    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({
            "last_run_at": now.isoformat(),
            "major_window_days": out.get("major_window_days"),
            "adoption_paused_until": out.get("adoption_paused_until"),
        }, indent=2, sort_keys=True))
        tmp.replace(state_path)
    except OSError:
        logger.debug("ul.idle: goodhart state write failed", exc_info=True)
    return out


# ── Registration helper ──────────────────────────────────────────────────


_RETENTION_STATE_FILE = ".retention_tick.json"


def _retention_state_path():
    from pathlib import Path
    import os
    override = os.getenv("UPGRADE_LIFECYCLE_DIR")
    if override:
        return Path(override) / _RETENTION_STATE_FILE
    try:
        from app.paths import WORKSPACE_ROOT
        return Path(WORKSPACE_ROOT) / "upgrade_lifecycle" / _RETENTION_STATE_FILE
    except Exception:
        return Path("/app/workspace/upgrade_lifecycle") / _RETENTION_STATE_FILE


def run_retention() -> dict:
    """P1#d — Weekly LIGHT pass for upgrade-lifecycle artefact retention.

    Compacts capability ledgers, prunes orphan trial results, caps
    the pending queue, and trims budget ledgers. Internal 7-day
    cadence guard prevents daily thrash.
    """
    import json
    from datetime import datetime, timezone
    out = {"status": "ok"}
    now = datetime.now(timezone.utc)
    state_path = _retention_state_path()

    try:
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            last_iso = state.get("last_run_at")
            if last_iso:
                last = datetime.fromisoformat(str(last_iso))
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if (now - last).total_seconds() < 7 * 24 * 3600:
                    out["status"] = "skipped_recent"
                    return out
    except Exception:
        pass

    try:
        from app.upgrade_lifecycle.retention import run_retention_pass
        result = run_retention_pass(now=now)
        out.update({k: v for k, v in result.items() if k != "started_at"})
    except Exception:
        logger.debug("ul.idle: retention pass raised", exc_info=True)
        out["status"] = "error"
        return out

    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({
            "last_run_at": now.isoformat(),
        }, indent=2, sort_keys=True))
        tmp.replace(state_path)
    except OSError:
        logger.debug("ul.idle: retention state write failed", exc_info=True)

    return out


def run_absence_policy() -> dict:
    """Daily tick — invokes :func:`absence_policy.evaluate`.

    Idempotent + fully gated by the master switch +
    operator_transition phase check. On every day the operator is
    present, this is a sub-millisecond no-op.
    """
    try:
        from app.upgrade_lifecycle.absence_policy import evaluate
    except Exception:
        logger.debug("ul.idle: absence_policy import failed", exc_info=True)
        return {"reason": "import_failed"}
    try:
        decision = evaluate()
        return {
            "eligible": decision.eligible,
            "reason": decision.reason,
            "auto_applied": list(decision.auto_applied),
        }
    except Exception:
        logger.debug("ul.idle: absence_policy raised", exc_info=True)
        return {"reason": "error"}


def get_idle_jobs() -> list[tuple[str, Callable[[], None], str]]:
    """Job tuples consumed by ``app.companion.loop.get_idle_jobs``."""
    from app.idle_scheduler import JobWeight
    return [
        ("upgrade-ecosystem-snapshot", run_annual_snapshot, JobWeight.LIGHT),
        ("upgrade-capability-adoption", run_capability_adoption, JobWeight.LIGHT),
        ("upgrade-lifecycle-goodhart", run_goodhart_throttle, JobWeight.LIGHT),
        ("upgrade-lifecycle-absence", run_absence_policy, JobWeight.LIGHT),
        ("upgrade-lifecycle-retention", run_retention, JobWeight.LIGHT),
    ]
