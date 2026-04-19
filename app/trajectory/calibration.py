"""
app.trajectory.calibration — Observer ↔ Attribution precision/recall.

Closes the loop the paper describes: the Observer predicts failure modes
BEFORE execution; the Attribution Analyzer identifies them AFTER. When
both run on the same trajectory, we have a labelled pair — over time
these pairs let us track Observer calibration per failure mode.

Data flow per trajectory:

  Observer fires (PRE_LLM_CALL)
      └── prediction captured in step.observer_prediction

  AttributionAnalyzer fires (post-crew, conditional)
      └── attribution.failure_mode is the post-hoc label

  record_calibration(trajectory, attribution)
      └── appends to a small JSONL log
      └── if the miss-rate for a mode exceeds a threshold over a window,
          emit OBSERVER_MIS_PREDICTION gap via gap_detector.

Storage: JSONL at workspace/trajectories/observer_calibration.jsonl
(append-only, ~200 bytes/entry). Kept separate from the attribution
sidecars so the windowed scan doesn't require full trajectory re-hydrates.

IMMUTABLE — infrastructure-level module.
"""

from __future__ import annotations

import json
import logging
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.trajectory.types import (
    AttributionRecord, Trajectory,
    STEP_PHASE_OBSERVER, FAILURE_MODE_NONE,
)

logger = logging.getLogger(__name__)

# Calibration log path — SINGLE source of truth lives in store.py
# (`store._CALIBRATION_LOG_PATH`). Writer and reader both resolve the
# path via `_log_path()` at call time so tests only need to monkeypatch
# one attribute. Kept as a property-style lookup to stay future-proof
# against further relocation.


def _log_path() -> Path:
    # Late import — avoid circularity and honour monkeypatches on either
    # module's `_LOG_PATH`/`_CALIBRATION_LOG_PATH` attribute.
    import sys as _sys
    cal_mod = _sys.modules.get("app.trajectory.calibration")
    if cal_mod is not None and hasattr(cal_mod, "_LOG_PATH"):
        # Test monkeypatches `cal_mod._LOG_PATH` — honour it.
        return cal_mod._LOG_PATH
    from app.trajectory.store import _CALIBRATION_LOG_PATH as _p
    return _p


# Module-level attribute — enables monkeypatching `cal_mod._LOG_PATH` in
# tests. When unset/None, `_log_path()` falls back to store's canonical.
_LOG_PATH: Optional[Path] = None

# Rolling window for precision/recall computation. Larger = more stable
# metrics, slower response to real regressions. 100 is a reasonable start.
_WINDOW_SIZE = 100

# Miss-rate thresholds that trigger OBSERVER_MIS_PREDICTION gap emission.
# Calibrated conservatively so one-off flukes don't spam the gap store.
_MIN_SAMPLES = 10          # Never emit before this many observations
_FP_RATE_THRESHOLD = 0.70  # Observer predicted X, attribution said "none" ≥70%
_FN_RATE_THRESHOLD = 0.70  # Attribution said X, Observer missed it ≥70%


# ── Flag check (single source of truth) ─────────────────────────────────

def _enabled() -> bool:
    try:
        from app.config import get_settings
        return bool(get_settings().observer_calibration_enabled)
    except Exception:
        return False


# ── Extract observer prediction from a trajectory ───────────────────────

def _observer_pred_from_trajectory(trajectory: Trajectory) -> dict:
    """Return the first Observer prediction captured in the trajectory.

    Usually there is at most one Observer firing per trajectory (the
    Observer runs once in `_run_crew` before dispatch). Return the
    payload dict, or {} if none found.
    """
    for step in trajectory.steps:
        if step.phase == STEP_PHASE_OBSERVER and step.observer_prediction:
            return dict(step.observer_prediction)
    return {}


# ── Public API ──────────────────────────────────────────────────────────

def record_calibration(
    trajectory: Trajectory, attribution: AttributionRecord,
) -> bool:
    """Append a calibration pair and scan for systemic mis-predictions.

    Returns True on successful append; False on error or when gated off.
    """
    if not _enabled():
        return False
    if trajectory is None or attribution is None:
        return False

    pred = _observer_pred_from_trajectory(trajectory)
    predicted_mode = str(pred.get("predicted_failure_mode") or FAILURE_MODE_NONE)
    predicted_conf = float(pred.get("confidence") or 0.0)
    actual_mode = str(attribution.failure_mode or FAILURE_MODE_NONE)

    row = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "trajectory_id": trajectory.trajectory_id,
        "crew_name": trajectory.crew_name,
        "predicted_mode": predicted_mode.lower(),
        "predicted_confidence": round(max(0.0, min(1.0, predicted_conf)), 3),
        "actual_mode": actual_mode.lower(),
        "attribution_verdict": attribution.verdict,
        "attribution_confidence": round(attribution.confidence, 3),
    }
    ok = _append(row)
    # Scan only on append success — no point scanning nothing.
    if ok:
        try:
            _scan_and_emit()
        except Exception:
            logger.debug("calibration._scan_and_emit failed", exc_info=True)
    return ok


def precision_recall_report() -> dict:
    """Compute per-failure-mode precision/recall over the recent window.

    Thin wrapper that delegates to `store.observer_calibration_report`.
    The read-side of the calibration pipeline lives in `store.py` so
    observability consumers (e.g. self_improvement.metrics) can aggregate
    without importing the write/eval surface of this module — preserving
    the CLAUDE.md safety invariant.

    Never raises.
    """
    try:
        from app.trajectory.store import observer_calibration_report
        return observer_calibration_report(_WINDOW_SIZE)
    except Exception:
        return {"samples": 0, "per_mode": {}}


# ── Internal: append + windowed read ────────────────────────────────────

def _append(row: dict) -> bool:
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except Exception:
        logger.debug("calibration._append failed", exc_info=True)
        return False


def _tail(n: int) -> list[dict]:
    path = _log_path()
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        # Use deque to keep memory bounded even if the log grows large.
        buf: deque[str] = deque(maxlen=n)
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                buf.append(line)
        for line in buf:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        logger.debug("calibration._tail failed", exc_info=True)
    return out


# ── Scan: trigger OBSERVER_MIS_PREDICTION gaps when a mode drifts ─────

def _scan_and_emit() -> int:
    """Inspect the recent window; emit gaps for modes that exceed thresholds.

    Returns the number of gaps emitted this scan. Idempotent at the store
    level — emit_gap dedups on (source, description) within 24h, so re-
    running this scan quickly doesn't flood the store.
    """
    rows = _tail(_WINDOW_SIZE)
    if len(rows) < _MIN_SAMPLES:
        return 0

    # Per-mode tally: { mode: {"fp": N, "fn": N, "tp": N} }
    tally: dict[str, dict[str, int]] = {}
    fp_samples: dict[str, list[str]] = {}
    fn_samples: dict[str, list[str]] = {}
    for r in rows:
        p = r.get("predicted_mode", "none")
        a = r.get("actual_mode", "none")
        tid = r.get("trajectory_id", "")
        if p != "none":
            if a == p:
                tally.setdefault(p, {"fp": 0, "fn": 0, "tp": 0})["tp"] += 1
            else:
                tally.setdefault(p, {"fp": 0, "fn": 0, "tp": 0})["fp"] += 1
                fp_samples.setdefault(p, []).append(tid)
        if a != "none" and a != p:
            tally.setdefault(a, {"fp": 0, "fn": 0, "tp": 0})["fn"] += 1
            fn_samples.setdefault(a, []).append(tid)

    emitted = 0
    try:
        from app.self_improvement.gap_detector import emit_observer_mis_prediction
    except Exception:
        return 0

    for mode, c in tally.items():
        predicted_count = c["tp"] + c["fp"]
        actual_count = c["tp"] + c["fn"]
        # False-positive rate: of times Observer cried wolf on mode,
        # how often was actual_mode something else?
        if predicted_count >= _MIN_SAMPLES:
            fp_rate = c["fp"] / max(1, predicted_count)
            if fp_rate >= _FP_RATE_THRESHOLD:
                if emit_observer_mis_prediction(
                    failure_mode=mode,
                    miss_kind="false_positive",
                    count=c["fp"],
                    window_n=predicted_count,
                    sample_trajectory_ids=fp_samples.get(mode, [])[:5],
                ):
                    emitted += 1
        # False-negative rate: of times the mode actually happened, how
        # often did Observer miss it?
        if actual_count >= _MIN_SAMPLES:
            fn_rate = c["fn"] / max(1, actual_count)
            if fn_rate >= _FN_RATE_THRESHOLD:
                if emit_observer_mis_prediction(
                    failure_mode=mode,
                    miss_kind="false_negative",
                    count=c["fn"],
                    window_n=actual_count,
                    sample_trajectory_ids=fn_samples.get(mode, [])[:5],
                ):
                    emitted += 1

    if emitted:
        logger.info(f"calibration: emitted {emitted} observer-mis-prediction gaps")
    return emitted
