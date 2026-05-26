"""Epistemic-gate health monitor (Stage C, 2026-05-26).

Weekly probe — watches the verdict telemetry from Stage B for behaviour
drift the operator should know about even before considering promotion.
Composes with the advisory report (which is operator-pulled, on-demand)
by pushing alerts when something needs attention.

Alert classes:

  * **silent_gate**: 7-day window has zero verdicts AND
    ``EPISTEMIC_ENABLED`` is on. Either the gate isn't being called, or
    verdict telemetry is failing — both warrant a Signal poke.

  * **drift_high**: 7-day would-have-blocked rate is >2× the 30-day
    baseline AND absolute rate >10%. Either a new producer source is
    misbehaving or the agent population is genuinely emitting more
    questionable claims; either way the operator should look.

  * **drift_low_zero**: 7-day rate is 0 BUT 30-day was non-zero. Catches
    the case where a detector was inadvertently disabled or a producer
    silently quietened.

  * **starved_gate**: ledger size p50 < 2 across the 7-day window AND
    producer is enabled. Producer isn't actually feeding the gate.

The monitor never auto-mutates anything — it surfaces signal to Signal
and (best-effort) the identity continuity ledger. Reliable observation
is the whole point; if you can't see the gate's behaviour you can't
promote it responsibly.
"""
from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Internal-cadence floor — even when invoked daily, only emit fresh
# alerts at this minimum interval to prevent Signal noise. The healing
# driver still walks us; we self-throttle.
_INTERNAL_CADENCE_S = 7 * 86400

# Drift detection parameters.
_DRIFT_RATIO_THRESHOLD = 2.0
_DRIFT_ABSOLUTE_THRESHOLD = 0.10
_STARVED_P50_THRESHOLD = 2
_SILENT_MIN_HOURS_AFTER_BOOT = 24  # don't alert silent_gate during first 24h

# State file — dedupe alerts at this path.
_STATE_FILE = "workspace/healing/epistemic_gate_health_state.json"


def _enabled() -> bool:
    try:
        from app.runtime_settings import get_epistemic_gate_health_monitor_enabled
        return bool(get_epistemic_gate_health_monitor_enabled())
    except Exception:
        return True  # default ON; this is observational


def _load_state() -> dict:
    from pathlib import Path
    import json
    p = Path(_STATE_FILE)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text() or "{}")
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    from pathlib import Path
    import json
    p = Path(_STATE_FILE)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state, default=str, indent=2))
    except Exception:
        logger.debug("epistemic_gate_health: state save failed", exc_info=True)


def _send_alert(alert_class: str, body: str) -> None:
    """Topic-keyed Signal alert. Failure-isolated."""
    try:
        from app.firebase.publish import publish_alert
        publish_alert(
            topic=f"epistemic_gate_health/{alert_class}",
            body=body,
            critical=False,
        )
    except Exception:
        logger.debug("epistemic_gate_health: publish_alert failed", exc_info=True)


def _emit_ledger_landmark(alert_class: str, snapshot: dict) -> None:
    """Record on the identity continuity ledger so annual reflection
    surfaces gate-health events alongside other identity-shaping changes."""
    try:
        from app.identity.continuity_ledger import record_event
        record_event(
            actor="epistemic_gate_health",
            summary=f"epistemic gate health alert: {alert_class}",
            detail=snapshot,
        )
    except Exception:
        logger.debug("epistemic_gate_health: ledger emit failed", exc_info=True)


def _gate_enabled() -> bool:
    try:
        from app.epistemic import is_enabled
        return bool(is_enabled())
    except Exception:
        return False


def _producer_enabled() -> bool:
    try:
        from app.runtime_settings import get_epistemic_retrieval_producer_enabled
        return bool(get_epistemic_retrieval_producer_enabled())
    except Exception:
        return False


def _maybe_alert(state: dict, alert_class: str, body: str, snapshot: dict) -> None:
    """Dedupe by (alert_class, week_of_year). Only one alert per week per class."""
    week_key = time.strftime("%Y-W%U", time.gmtime())
    fired = state.get(alert_class, {}).get("week_key")
    if fired == week_key:
        return
    _send_alert(alert_class, body)
    _emit_ledger_landmark(alert_class, snapshot)
    state.setdefault(alert_class, {})["week_key"] = week_key
    state[alert_class]["fired_at"] = time.time()


def run() -> dict[str, Any]:
    """One monitor pass. Returns telemetry dict for the driver."""
    if not _enabled():
        return {"status": "disabled"}

    try:
        from app.observability.epistemic_advisory_report import report
        last_7d = report(window_days=7)
        last_30d = report(window_days=30)
    except Exception:
        logger.debug("epistemic_gate_health: report unavailable", exc_info=True)
        return {"status": "report_unavailable"}

    state = _load_state()
    gate_on = _gate_enabled()
    producer_on = _producer_enabled()

    n7 = int(last_7d.get("total_verdicts", 0))
    n30 = int(last_30d.get("total_verdicts", 0))
    rate7 = float(last_7d.get("would_have_blocked_rate", 0.0))
    rate30 = float(last_30d.get("would_have_blocked_rate", 0.0))
    p50 = last_7d.get("ledger_size_pct", {}).get("p50") or 0

    # ── silent_gate ─────────────────────────────────────────────────
    if gate_on and n7 == 0:
        _maybe_alert(
            state, "silent_gate",
            "epistemic gate has produced 0 verdicts in 7d while EPISTEMIC_ENABLED "
            "is on — telemetry hook may be broken or gate isn't being reached.",
            {"n7": n7, "gate_on": gate_on, "n30": n30},
        )

    # ── drift_high ──────────────────────────────────────────────────
    if n7 >= 50 and rate30 > 0 and rate7 / rate30 >= _DRIFT_RATIO_THRESHOLD \
            and rate7 > _DRIFT_ABSOLUTE_THRESHOLD:
        _maybe_alert(
            state, "drift_high",
            f"epistemic gate would-have-blocked rate spiked 7d={rate7:.1%} vs "
            f"30d={rate30:.1%} (ratio={rate7/rate30:.1f}x). Inspect top reasons in "
            "`aai advisory epistemic` before promoting.",
            {"rate7": rate7, "rate30": rate30, "n7": n7},
        )

    # ── drift_low_zero ──────────────────────────────────────────────
    if n7 >= 50 and rate7 == 0.0 and rate30 > 0.01:
        _maybe_alert(
            state, "drift_low_zero",
            f"epistemic gate has stopped flagging anything (7d rate=0%, "
            f"30d rate={rate30:.1%}). A detector may have regressed silently.",
            {"rate7": rate7, "rate30": rate30, "n7": n7},
        )

    # ── starved_gate ────────────────────────────────────────────────
    if producer_on and n7 >= 20 and p50 < _STARVED_P50_THRESHOLD:
        _maybe_alert(
            state, "starved_gate",
            f"epistemic gate is being called with empty/thin ledgers "
            f"(p50={p50}). Producer is enabled but not feeding claims. "
            "Check `app/epistemic/retrieval_producer.py` wiring.",
            {"p50": p50, "n7": n7, "producer_on": producer_on},
        )

    _save_state(state)
    return {
        "status": "ok",
        "n7": n7, "n30": n30,
        "rate7": rate7, "rate30": rate30,
        "gate_on": gate_on,
        "producer_on": producer_on,
    }
