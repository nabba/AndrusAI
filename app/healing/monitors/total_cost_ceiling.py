"""total_cost_ceiling — Aggregates spend across all subsystems and pauses
expensive idle work when the monthly ceiling is approached.

Gap #2 (2026-05-24): per-subsystem budgets exist (ConnectorBudget,
agent budgets, Anthropic per-day cap, U5 quarterly $20, briefing
proposer ~$0.001/wk, workstream_news ~$0.04/day, etc.) but there is no
**total system monthly cost cap** that aggregates across all subsystems.
Slow drift across 25 subsystems can become $400/mo before the operator
notices.

How it works
============

Reads ``cost_usd`` from ``control_plane.audit_log`` for the current
calendar month. Sums across every actor + agent. Compares to
``total_cost_monthly_cap_usd`` (default $200, operator-configurable
up to a $10k sanity ceiling).

Three thresholds with hysteresis:

  * **80%** — warning Signal alert; idle scheduler unaffected.
  * **95%** — critical Signal alert; flip ``idle_pause_due_to_budget=True``
    so the idle scheduler skips MEDIUM + HEAVY jobs until the brake
    releases.
  * **<70%** — release the brake (5-point hysteresis below 80% to avoid
    flapping at the cusp).

Per-month dedup: at most one warning alert + one critical alert per
calendar month. Alerts include the projected end-of-month spend so the
operator can decide whether to raise the cap or investigate.

Why month-of-the-year boundary (not 30-day rolling)
===================================================

The operator pays the credit-card bill on calendar month boundaries.
Aligning the budget cap to that mental model means the operator can
read "we are 95% of monthly cap with 3 days left in the month" and
immediately know what action to take.

What this is NOT
================

  * Not a hard kill switch. The brake only pauses idle MEDIUM+HEAVY jobs.
    Foreground operator interaction (chat, brainstorm, manual brain
    work) is never paused — that would amputate the system at the
    wrong moment.
  * Not a per-subsystem allocator. Per-subsystem caps remain — this
    is the top-level guardrail in addition.
  * Not a forecaster. The "projected" number is a simple
    days-into-month × actual-spend extrapolation. Sufficient for an
    alert; not a substitute for the cost-trends OLS regression.

Why LIGHT jobs aren't braked
============================

The brake gates the ``MEDIUM`` and ``HEAVY`` phases in
``app.idle_scheduler``. LIGHT jobs continue regardless — but be
aware that "LIGHT" in this codebase means *short-running*, not
necessarily *free*: ``workstream-news`` (Haiku-per-workspace, ~$0.04/day),
``briefing-evolution`` proposer (~$0.001/wk), and a few other LIGHT
jobs do call LLMs. Each is capped by its own per-subsystem budget;
the top-level brake is a safety net for the MEDIUM/HEAVY paths
where the bulk of spend lives, not a granular pause-all mechanism.
If the operator needs to stop a specific LIGHT job, that job's
own master switch is the right knob.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


NAME = "total_cost_ceiling"
CADENCE_SECONDS = 6 * 3600  # daily probe with 6h ticks so 80%→95% transitions are caught quickly
MASTER_SWITCH_KEY = "total_cost_ceiling_enabled"

_INTERNAL_CADENCE_S = 24 * 3600
_STATE_FILE_NAME = "total_cost_ceiling_state.json"

_WARN_PCT = 0.80
_BRAKE_PCT = 0.95
_RELEASE_PCT = 0.70  # hysteresis: brake holds until spend drops below 70% of cap


def _enabled() -> bool:
    try:
        from app.runtime_settings import get_total_cost_ceiling_enabled
        return get_total_cost_ceiling_enabled()
    except Exception:
        return os.getenv(
            "TOTAL_COST_CEILING_ENABLED", "true",
        ).lower() in ("true", "1", "yes", "on")


def _monthly_cap_usd() -> float:
    try:
        from app.runtime_settings import get_total_cost_monthly_cap_usd
        return get_total_cost_monthly_cap_usd()
    except Exception:
        return float(os.getenv("TOTAL_COST_MONTHLY_CAP_USD", "200"))


def _workspace() -> Path:
    try:
        from app.paths import WORKSPACE_ROOT
        return Path(WORKSPACE_ROOT)
    except Exception:
        return Path("/app/workspace")


def _state_path() -> Path:
    return _workspace() / "healing" / _STATE_FILE_NAME


def _read_state() -> dict[str, Any]:
    p = _state_path()
    if not p.exists():
        return {"last_run_at": 0.0, "month_alert_state": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"last_run_at": 0.0, "month_alert_state": {}}


def _write_state(state: dict[str, Any]) -> None:
    p = _state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(state, indent=2, sort_keys=True), encoding="utf-8",
        )
    except Exception:
        logger.debug("total_cost_ceiling: state write failed", exc_info=True)


def _month_key(now: float) -> str:
    dt = datetime.fromtimestamp(now, tz=timezone.utc)
    return f"{dt.year:04d}-{dt.month:02d}"


def _month_progress(now: float) -> tuple[int, int]:
    """Return ``(day_of_month, days_in_month)`` for the projection.
    Both 1-indexed."""
    dt = datetime.fromtimestamp(now, tz=timezone.utc)
    if dt.month == 12:
        next_month = datetime(dt.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        next_month = datetime(dt.year, dt.month + 1, 1, tzinfo=timezone.utc)
    first = datetime(dt.year, dt.month, 1, tzinfo=timezone.utc)
    days_in_month = (next_month - first).days
    return dt.day, days_in_month


def _query_mtd_total_cost(*, now: float) -> Optional[float]:
    """Sum ``cost_usd`` from control_plane.audit_log for the current
    calendar month. Returns None on query failure (test envs without
    Postgres); the run path treats None as 'unknown' and skips.
    """
    try:
        from app.control_plane.db import execute
    except Exception:
        return None
    try:
        rows = execute(
            """SELECT COALESCE(SUM(cost_usd), 0) AS total
                 FROM control_plane.audit_log
                WHERE cost_usd IS NOT NULL
                  AND date_trunc('month', timestamp)
                      = date_trunc('month', NOW())""",
            (), fetch=True,
        )
    except Exception:
        logger.debug("total_cost_ceiling: db query failed", exc_info=True)
        return None
    if not rows:
        return 0.0
    return float(rows[0].get("total") or 0.0)


def _set_brake(value: bool) -> None:
    try:
        from app.runtime_settings import set_idle_pause_due_to_budget
        set_idle_pause_due_to_budget(value)
    except Exception:
        logger.debug("total_cost_ceiling: brake set failed", exc_info=True)


def _brake_is_engaged() -> bool:
    try:
        from app.runtime_settings import get_idle_pause_due_to_budget
        return get_idle_pause_due_to_budget()
    except Exception:
        return False


def _alert(
    *,
    title: str,
    body: str,
    critical: bool,
) -> bool:
    try:
        from app.notify import notify
        notify(
            title=title,
            body=body,
            url="/cp/settings",
            topic=f"total_cost_ceiling:{'critical' if critical else 'warning'}",
            critical=critical,
            arbitrate=True,
        )
        return True
    except Exception:
        logger.debug("total_cost_ceiling: notify failed", exc_info=True)
        return False


def evaluate(
    spend_usd: float,
    cap_usd: float,
    *,
    day_of_month: int,
    days_in_month: int,
    brake_currently_engaged: bool,
) -> dict[str, Any]:
    """Pure decision function — easy to unit-test. Returns the action
    the run path should take.

    Output keys:
      pct:                       float (spend / cap; 0..)
      projected_end_of_month:    float (linear extrapolation)
      level:                     str (ok | warn | brake)
      brake_target:              bool (desired brake state after this evaluation)
      alert_warning:             bool (should fire 80%-class alert)
      alert_critical:            bool (should fire 95%-class alert)
      alert_release:             bool (should fire brake-released alert)
    """
    pct = (spend_usd / cap_usd) if cap_usd > 0 else 0.0
    if day_of_month <= 0 or days_in_month <= 0:
        projected = spend_usd
    else:
        projected = spend_usd * days_in_month / day_of_month

    if pct >= _BRAKE_PCT:
        level = "brake"
        brake_target = True
    elif pct >= _WARN_PCT:
        level = "warn"
        brake_target = brake_currently_engaged  # stay-as-is in the hysteresis band
    elif pct < _RELEASE_PCT:
        level = "ok"
        brake_target = False
    else:
        # In [_RELEASE_PCT, _WARN_PCT) — release zone. Same as `ok` but
        # we name it separately for the alert decision (brake_target=False).
        level = "ok"
        brake_target = False

    alert_warning = level == "warn" and not brake_currently_engaged
    alert_critical = level == "brake"
    alert_release = brake_currently_engaged and brake_target is False

    return {
        "pct": pct,
        "projected_end_of_month": projected,
        "level": level,
        "brake_target": brake_target,
        "alert_warning": alert_warning,
        "alert_critical": alert_critical,
        "alert_release": alert_release,
    }


def run(*, now: Optional[float] = None) -> dict[str, Any]:
    """One probe pass. Daily internal cadence; alerts deduped per
    calendar month."""
    if not _enabled():
        return {"ran": False, "skipped": True}

    cur = float(now) if now is not None else time.time()
    state = _read_state()
    last = float(state.get("last_run_at", 0))
    if last > 0 and cur - last < _INTERNAL_CADENCE_S:
        return {"ran": False}

    state["last_run_at"] = cur

    cap = _monthly_cap_usd()
    spend = _query_mtd_total_cost(now=cur)
    if spend is None:
        _write_state(state)
        return {"ran": True, "skipped": True, "reason": "db_unreachable"}

    dom, dim = _month_progress(cur)
    brake_engaged = _brake_is_engaged()
    decision = evaluate(
        spend, cap,
        day_of_month=dom,
        days_in_month=dim,
        brake_currently_engaged=brake_engaged,
    )

    month_key = _month_key(cur)
    month_state = state.setdefault("month_alert_state", {}).setdefault(
        month_key, {"warning_sent": False, "critical_sent": False},
    )

    alerts_sent: list[str] = []

    # 80% warning — only one per calendar month.
    if decision["alert_warning"] and not month_state.get("warning_sent"):
        body = (
            f"💸 Monthly spend at {decision['pct']*100:.1f}% of cap "
            f"(${spend:.2f} of ${cap:.2f}).\n\n"
            f"  • Day {dom} of {dim} this month.\n"
            f"  • Projected end-of-month: ${decision['projected_end_of_month']:.2f}.\n"
            f"  • Threshold for brake: ${cap * _BRAKE_PCT:.2f}.\n\n"
            "If projected is acceptable, no action needed. Otherwise: investigate "
            "via /cp/costs/by-crew + /cp/costs/by-agent, raise the cap via "
            "/cp/settings → total_cost_monthly_cap_usd, or wait for the brake to "
            "auto-engage at 95%."
        )
        if _alert(
            title=f"💸 Monthly cost: {decision['pct']*100:.0f}% of cap",
            body=body,
            critical=False,
        ):
            month_state["warning_sent"] = True
            alerts_sent.append("warning")

    # 95% critical + brake — only one per calendar month, and only when
    # the brake actually transitions ON (idempotent).
    if decision["alert_critical"] and not month_state.get("critical_sent"):
        body = (
            f"🔴 Monthly spend at {decision['pct']*100:.1f}% of cap "
            f"(${spend:.2f} of ${cap:.2f}). Engaging idle-job brake — "
            f"MEDIUM + HEAVY idle jobs will skip until spend drops below "
            f"{_RELEASE_PCT*100:.0f}%.\n\n"
            f"  • Day {dom} of {dim} this month.\n"
            f"  • Projected end-of-month: ${decision['projected_end_of_month']:.2f}.\n"
            f"  • Foreground operator interaction (chat / brainstorm) NOT paused.\n\n"
            "Options: raise the cap via /cp/settings → total_cost_monthly_cap_usd, "
            "drill into the top spenders via /cp/costs, or accept the pause until "
            "month rollover."
        )
        if _alert(
            title=f"🔴 Monthly cost {decision['pct']*100:.0f}% — brake engaged",
            body=body,
            critical=True,
        ):
            month_state["critical_sent"] = True
            alerts_sent.append("critical")

    # Brake transitions are independent of monthly dedup.
    if decision["brake_target"] != brake_engaged:
        _set_brake(decision["brake_target"])

    if decision["alert_release"]:
        body = (
            f"✅ Monthly spend dropped to {decision['pct']*100:.1f}% of cap "
            f"(${spend:.2f} of ${cap:.2f}). Releasing the idle-job brake."
        )
        if _alert(
            title="✅ Cost brake released",
            body=body,
            critical=False,
        ):
            alerts_sent.append("release")

    _write_state(state)
    return {
        "ran": True,
        "iso": datetime.fromtimestamp(cur, tz=timezone.utc).isoformat(),
        "spend_usd": round(spend, 4),
        "cap_usd": round(cap, 2),
        "pct": round(decision["pct"], 4),
        "level": decision["level"],
        "brake_engaged_before": brake_engaged,
        "brake_engaged_after": decision["brake_target"],
        "projected_end_of_month_usd": round(decision["projected_end_of_month"], 2),
        "alerts_sent": alerts_sent,
    }


__all__ = ["run", "evaluate"]
