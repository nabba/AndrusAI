"""python_eol_proximity — quarterly probe of Python version EOL window.

PROGRAM §63 — U8. Reads the hardcoded EOL table from
:mod:`app.upgrade_lifecycle.ecosystem_snapshot` and fires escalating
Signal alerts at the 12-month / 6-month / 3-month / 1-month
thresholds before the active Python minor version goes end-of-life.

Each threshold fires AT MOST ONCE per (year, threshold) — once the
operator has seen the alert, it's deduped via persistent state so
the daily loop doesn't re-spam. New thresholds light up as the EOL
date approaches.

Why a separate monitor (not just the annual snapshot): the snapshot
runs once per year in January, but EOL transitions happen mid-year.
A user who skipped the January reading needs a louder signal as the
deadline approaches.

Master switch: ``python_eol_proximity_monitor_enabled`` (default ON).
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

NAME = "python_eol_proximity"
CADENCE_SECONDS = 24 * 3600           # daily probe
INTERNAL_QUARTERLY_S = 90 * 24 * 3600
MASTER_SWITCH_KEY = "python_eol_proximity_monitor_enabled"


_THRESHOLDS_DAYS: tuple[tuple[int, str], ...] = (
    (30, "≤ 1 month"),
    (90, "≤ 3 months"),
    (180, "≤ 6 months"),
    (365, "≤ 12 months"),
)


def _state_path() -> Path:
    try:
        from app.paths import WORKSPACE_ROOT
        return Path(WORKSPACE_ROOT) / "healing" / ".python_eol_proximity_state.json"
    except Exception:
        return Path("/app/workspace/healing/.python_eol_proximity_state.json")


def _enabled() -> bool:
    try:
        from app.runtime_settings import (
            get_python_eol_proximity_monitor_enabled,
        )
        return get_python_eol_proximity_monitor_enabled()
    except Exception:
        return True


def _read_state() -> dict:
    import json
    p = _state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(state: dict) -> None:
    import json
    p = _state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
        tmp.replace(p)
    except OSError:
        logger.debug("python_eol_proximity: state write failed", exc_info=True)


def _current_python_minor() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def _eol_date_for(minor: str) -> Optional[date]:
    try:
        from app.upgrade_lifecycle.ecosystem_snapshot import PYTHON_EOL_TABLE
        return PYTHON_EOL_TABLE.get(minor)
    except Exception:
        return None


def _notify(title: str, body: str, topic: str, *, critical: bool = False) -> None:
    try:
        from app.notify import notify
        notify(title=title, body=body, url="/cp/ecosystem",
              topic=topic, critical=critical, arbitrate=True)
    except Exception:
        logger.debug("python_eol_proximity: notify failed", exc_info=True)


def run() -> None:
    """Driver entry — daily probe, internal quarterly cadence with
    additional 'crossed a threshold' override."""
    if not _enabled():
        return
    now_ts = time.time()
    state = _read_state()
    last_run = float(state.get("last_run_at") or 0.0)

    current_minor = _current_python_minor()
    eol_date = _eol_date_for(current_minor)

    if eol_date is None:
        # Unknown EOL — quarterly noise about "we should add this version
        # to the table." Only fire once.
        if not state.get(f"unknown_{current_minor}"):
            _notify(
                title="🐍 Python EOL unknown",
                body=(
                    f"Python {current_minor} is running but no EOL date "
                    f"is recorded in PYTHON_EOL_TABLE. Add an entry to "
                    f"app.upgrade_lifecycle.ecosystem_snapshot."
                ),
                topic=f"py_eol_unknown:{current_minor}",
            )
            state[f"unknown_{current_minor}"] = True
            state["last_run_at"] = now_ts
            _write_state(state)
        return

    today = date.today()
    days_until = (eol_date - today).days
    fired_key = f"fired_{current_minor}"
    fired_for_version = state.get(fired_key) or []

    new_alerts: list[tuple[int, str]] = []
    for threshold_days, label in _THRESHOLDS_DAYS:
        if days_until <= threshold_days and threshold_days not in fired_for_version:
            new_alerts.append((threshold_days, label))

    # Fire newest (tightest) threshold first.
    new_alerts.sort()
    for threshold_days, label in new_alerts:
        critical = threshold_days <= 90    # ≤3-month and ≤1-month are critical
        _notify(
            title=f"🐍 Python EOL {label}",
            body=(
                f"Python {current_minor} EOL is on {eol_date.isoformat()} — "
                f"{days_until} days from today. Plan an upgrade window "
                f"and put the major bump in the annual ecosystem snapshot."
            ),
            topic=f"py_eol:{current_minor}:{threshold_days}",
            critical=critical,
        )
        fired_for_version.append(threshold_days)

    if new_alerts:
        state[fired_key] = sorted(set(fired_for_version))
        state["last_run_at"] = now_ts
        _write_state(state)
        return

    # No new alerts — only persist on quarterly cadence so the
    # state file doesn't churn.
    if last_run == 0 or (now_ts - last_run) >= INTERNAL_QUARTERLY_S:
        state["last_run_at"] = now_ts
        _write_state(state)
