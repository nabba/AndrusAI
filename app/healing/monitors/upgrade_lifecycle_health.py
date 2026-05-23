"""upgrade_lifecycle_health — proactive monitor for the U1–U6 pipeline.

PROGRAM §63 — U8. Weekly probe (internal cadence) that surfaces three
silent-failure conditions in the upgrade-lifecycle subsystem:

  * **Capability-extraction backlog stuck** — capabilities directory
    is over 30 days old without new rows (extractor daemon wedged).
  * **Trial runner repeated failure** — same ``(package, to_version)``
    failed ≥ 5 times in 30 days (likely a pinning regression masked
    as a flaky trial).
  * **Budget burn-rate runaway** — quarterly LLM budget over 80 %
    consumed in first half of quarter (Goodhart guard — adoption
    proposer is generating too many CRs).

Master switch: ``upgrade_lifecycle_health_monitor_enabled`` (default ON).
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

NAME = "upgrade_lifecycle_health"
CADENCE_SECONDS = 24 * 3600           # daily probe
INTERNAL_WEEKLY_S = 7 * 24 * 3600
MASTER_SWITCH_KEY = "upgrade_lifecycle_health_monitor_enabled"


_STATE_FILENAME = ".upgrade_lifecycle_health_state.json"
_BACKLOG_STALENESS_DAYS = 30
_TRIAL_FAILURE_THRESHOLD = 5
_TRIAL_FAILURE_WINDOW_DAYS = 30
_BUDGET_BURN_THRESHOLD = 0.80


def _state_path() -> Path:
    try:
        from app.paths import WORKSPACE_ROOT
        return Path(WORKSPACE_ROOT) / "healing" / _STATE_FILENAME
    except Exception:
        return Path("/app/workspace/healing") / _STATE_FILENAME


def _enabled() -> bool:
    try:
        from app.runtime_settings import (
            get_upgrade_lifecycle_health_monitor_enabled,
        )
        return get_upgrade_lifecycle_health_monitor_enabled()
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
        logger.debug("ul.health: state write failed", exc_info=True)


# ── Check primitives ────────────────────────────────────────────────────


def _check_capability_backlog_stale(now: datetime) -> Optional[str]:
    """Return an alert string if the capability dir hasn't seen any new
    rows in 30 days, None otherwise."""
    try:
        from app.upgrade_lifecycle.changelog_fetcher import _capabilities_dir
        cap_dir = _capabilities_dir()
    except Exception:
        return None
    if not cap_dir.exists():
        # Never extracted — first-run grace.
        return None
    newest_mtime = 0.0
    try:
        for path in cap_dir.glob("*.jsonl"):
            try:
                mtime = path.stat().st_mtime
                if mtime > newest_mtime:
                    newest_mtime = mtime
            except OSError:
                continue
    except OSError:
        return None
    if newest_mtime <= 0:
        return None
    age_days = (now.timestamp() - newest_mtime) / 86400.0
    if age_days < _BACKLOG_STALENESS_DAYS:
        return None
    return (
        f"capability extraction backlog stale — newest row {age_days:.0f}d old"
    )


def _check_repeated_trial_failure(now: datetime) -> list[str]:
    """Return zero or more alert strings for packages whose trial has
    failed ≥ 5 times in the past 30 days."""
    try:
        from app.upgrade_lifecycle.orchestrator import _trials_dir
        trials_dir = _trials_dir()
    except Exception:
        return []
    if not trials_dir.exists():
        return []

    import json
    cutoff = (now - timedelta(days=_TRIAL_FAILURE_WINDOW_DAYS)).isoformat()
    failures: dict[tuple[str, str], int] = {}

    # We don't have a structured trial-attempt log yet (out of scope
    # for v1); use the current persisted result as a proxy: if it's
    # in a "failed" status, count as one failure. A future trial
    # scheduler can write per-attempt rows for finer accounting.
    for path in trials_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        status = data.get("status", "")
        if status not in ("test_failure", "install_failure",
                         "timeout", "infrastructure_error"):
            continue
        try:
            mtime = path.stat().st_mtime
            ts = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
        except OSError:
            continue
        if ts < cutoff:
            continue
        key = (data.get("package", ""), data.get("to_version", ""))
        failures[key] = failures.get(key, 0) + 1

    alerts: list[str] = []
    for (pkg, ver), count in failures.items():
        if count >= _TRIAL_FAILURE_THRESHOLD:
            alerts.append(
                f"trial repeatedly failing for {pkg}=={ver}: "
                f"{count}× in past {_TRIAL_FAILURE_WINDOW_DAYS}d"
            )
    return alerts


def _check_snapshot_unread(now: datetime) -> Optional[str]:
    """A6-P1 — alert when current-year ecosystem snapshot has been
    sitting unread for > 90 days.

    The annual snapshot generates in January; if the operator never
    opens ``/cp/ecosystem``, rows stay ``proposed`` forever. After
    90 days of zero acceptances, the silence is itself a signal
    (operator absent OR snapshot is fundamentally not useful).
    """
    try:
        from app.upgrade_lifecycle.ecosystem_snapshot import (
            _read_snapshot,
            _snapshot_dir,
        )
    except Exception:
        return None
    snap = _read_snapshot(now.year)
    if snap is None:
        # No snapshot yet — separate concern (January didn't fire).
        return None
    try:
        generated_at = datetime.fromisoformat(snap.generated_at)
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
    age_days = (now - generated_at).days
    if age_days < 90:
        return None
    if not snap.major_upgrades:
        # Empty plan — no acceptances expected. Silence is fine.
        return None
    accepted = sum(1 for m in snap.major_upgrades if m.status == "accepted")
    if accepted > 0:
        return None
    return (
        f"ecosystem snapshot {snap.year} has been unread for "
        f"{age_days}d — {len(snap.major_upgrades)} pending upgrade rows, "
        f"0 accepted. Open /cp/ecosystem to review."
    )


def _check_budget_burn(now: datetime) -> Optional[str]:
    """Alert when first-half-of-quarter spend > 80 % of budget."""
    try:
        from app.upgrade_lifecycle.capability_adoption import (
            _quarterly_budget_usd,
            current_quarter_spend,
        )
    except Exception:
        return None
    budget = _quarterly_budget_usd()
    if budget <= 0:
        return None
    spend = current_quarter_spend(now=now)
    burn_ratio = spend / budget if budget > 0 else 0.0
    # First-half check: only alert if we're in the first half of a quarter.
    q_start = datetime(
        now.year,
        ((now.month - 1) // 3) * 3 + 1,
        1, tzinfo=timezone.utc,
    )
    q_days_in = (now - q_start).days
    if q_days_in > 45 or burn_ratio < _BUDGET_BURN_THRESHOLD:
        return None
    return (
        f"quarterly budget burn-rate runaway — {burn_ratio:.0%} spent "
        f"by day {q_days_in} of quarter (limit {budget:.2f}, used {spend:.2f})"
    )


# ── Public driver entry ──────────────────────────────────────────────────


def _notify(title: str, body: str, topic: str) -> None:
    try:
        from app.notify import notify
        notify(title=title, body=body, url="/cp/settings",
              topic=topic, critical=False, arbitrate=True)
    except Exception:
        logger.debug("ul.health: notify failed", exc_info=True)


def run() -> None:
    """Driver entry — daily check, weekly internal cadence guard."""
    if not _enabled():
        return
    now = datetime.now(timezone.utc)
    state = _read_state()
    last_run = float(state.get("last_run_at") or 0.0)
    if last_run > 0 and (now.timestamp() - last_run) < INTERNAL_WEEKLY_S:
        return

    alerts: list[str] = []
    backlog = _check_capability_backlog_stale(now)
    if backlog:
        alerts.append(backlog)
    unread = _check_snapshot_unread(now)
    if unread:
        alerts.append(unread)
    trial_alerts = _check_repeated_trial_failure(now)
    alerts.extend(trial_alerts)
    burn = _check_budget_burn(now)
    if burn:
        alerts.append(burn)

    for alert in alerts:
        # Topic-key so the arbiter dedups same-pattern alerts.
        topic = "ul_health:" + alert.split(" ")[0]
        _notify(
            title="📦 Upgrade-lifecycle health",
            body=alert,
            topic=topic,
        )

    state["last_run_at"] = now.timestamp()
    state["last_alert_count"] = len(alerts)
    _write_state(state)
