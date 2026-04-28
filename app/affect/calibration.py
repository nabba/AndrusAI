"""
calibration.py — Daily reflection cycle scaffold.

Phase 1 SCOPE: this module reads recent affect trace, replays the reference
panel, computes the healthy-dynamics predicate, and writes a reflection
report. It does NOT yet propose or apply calibration deltas — that is
Phase 2 work.

The scheduled task is registered separately in hooks.install() at process
startup; this module exposes only the cycle entry point.

Output: /app/workspace/affect/reflections/YYYY-MM-DD.json
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.affect.schemas import AffectState, utc_now_iso

logger = logging.getLogger(__name__)

_AFFECT_DIR = Path("/app/workspace/affect")
_TRACE_FILE = _AFFECT_DIR / "trace.jsonl"
_REFLECTIONS_DIR = _AFFECT_DIR / "reflections"


# ── Trace replay ────────────────────────────────────────────────────────────


def load_recent_trace(hours: int = 24) -> list[AffectState]:
    """Read the last N hours of affect trace as AffectState objects."""
    states, _ = load_recent_trace_with_viability(hours)
    return states


def load_recent_trace_with_viability(hours: int = 24) -> tuple[list[AffectState], list[dict]]:
    """Read the last N hours of trace; return (affect_states, viability_frames).

    Two parallel lists, same length. viability_frames are the raw dicts
    persisted alongside each affect snapshot in trace.jsonl. Used by the
    Phase-2 calibration backtest.
    """
    if not _TRACE_FILE.exists():
        return [], []
    cutoff = (datetime.now(timezone.utc).timestamp() - hours * 3600)
    states: list[AffectState] = []
    frames: list[dict] = []
    try:
        with _TRACE_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                affect = row.get("affect", {})
                viability = row.get("viability", {})
                ts_str = affect.get("ts", "")
                try:
                    if datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp() < cutoff:
                        continue
                except ValueError:
                    continue
                states.append(AffectState(
                    valence=float(affect.get("valence", 0.0)),
                    arousal=float(affect.get("arousal", 0.0)),
                    controllability=float(affect.get("controllability", 0.5)),
                    valence_source=str(affect.get("valence_source", "")),
                    arousal_source=str(affect.get("arousal_source", "")),
                    controllability_source=str(affect.get("controllability_source", "")),
                    attractor=str(affect.get("attractor", "neutral")),
                    internal_state_id=affect.get("internal_state_id"),
                    viability_frame_ts=affect.get("viability_frame_ts"),
                    ts=ts_str,
                ))
                frames.append(viability)
    except Exception:
        logger.debug("calibration: trace read failed", exc_info=True)
    return states, frames


# ── Reflection cycle entry ──────────────────────────────────────────────────


def run_reflection_cycle(window_hours: int = 24) -> dict:
    """Run the daily reflection. Returns the report dict (also written to disk).

    Phase 2: calibration deltas are now proposed and (when guardrails pass)
    applied. The 6-guardrail flow lives in calibration_proposals.evaluate_and_apply.
    """
    from app.affect.welfare import healthy_dynamics_predicate, read_audit
    from app.affect.reference_panel import replay_panel
    from app.affect.calibration_proposals import evaluate_and_apply

    window, viability_frames = load_recent_trace_with_viability(hours=window_hours)
    if window:
        valences = [s.valence for s in window]
        arousals = [s.arousal for s in window]
        controllabilities = [s.controllability for s in window]
        attractor_counts: dict[str, int] = {}
        for s in window:
            attractor_counts[s.attractor] = attractor_counts.get(s.attractor, 0) + 1

        stats = {
            "n": len(window),
            "mean_valence": round(sum(valences) / len(valences), 4),
            "mean_arousal": round(sum(arousals) / len(arousals), 4),
            "mean_controllability": round(sum(controllabilities) / len(controllabilities), 4),
            "attractor_counts": attractor_counts,
        }
    else:
        stats = {"n": 0}

    healthy, diags = healthy_dynamics_predicate(window) if window else (False, {"reason": "no_trace"})

    panel_results = replay_panel()
    drift_counts = {"ok": 0, "numbness": 0, "over_reactive": 0, "wrong_attractor": 0, "drift": 0}
    for r in panel_results:
        drift_counts[r.drift_signature] = drift_counts.get(r.drift_signature, 0) + 1

    audit_window = read_audit(limit=200)
    # Filter to window
    audit_in_window = []
    cutoff = (datetime.now(timezone.utc).timestamp() - window_hours * 3600)
    for a in audit_window:
        try:
            if datetime.fromisoformat(a.get("ts", "").replace("Z", "+00:00")).timestamp() >= cutoff:
                audit_in_window.append(a)
        except (ValueError, AttributeError):
            continue

    report = {
        "ts": utc_now_iso(),
        "window_hours": window_hours,
        "stats": stats,
        "healthy_dynamics": {"passes": healthy, "diagnostics": diags},
        "reference_panel": {
            "drift_counts": drift_counts,
            "results": [r.to_dict() for r in panel_results],
        },
        "welfare_audit_in_window": audit_in_window,
        "calibration_proposal": evaluate_and_apply(
            affect_history=window,
            viability_window=viability_frames,
        ),
        "attachment": _check_attachment_at_reflection(),
    }

    _write_report(report)
    logger.info(
        f"affect.calibration: reflection complete — n={stats.get('n', 0)} healthy={healthy} "
        f"drift={drift_counts}"
    )
    return report


def _check_attachment_at_reflection() -> dict:
    """Phase 3: during the daily reflection, evaluate separation analog status
    and care policy modifiers. Latent only — no auto-actions are taken.
    """
    out: dict = {"phase": "phase-3", "candidates_generated": []}
    try:
        from app.affect.attachment import (
            check_separation_analog,
            list_all_others,
            primary_user_identity,
        )
        from app.affect.care_policies import current_modifiers

        # Generate (at most one per cooldown) check-in candidate for the primary user
        cand = check_separation_analog(primary_user_identity())
        if cand is not None:
            out["candidates_generated"].append(cand)

        out["modifiers"] = current_modifiers().to_dict()
        out["others"] = [m.to_dict() for m in list_all_others()]
    except Exception:
        logger.debug("affect.calibration: attachment check failed", exc_info=True)
    return out


def _write_report(report: dict) -> None:
    try:
        _REFLECTIONS_DIR.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = _REFLECTIONS_DIR / f"{date_str}.json"
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    except Exception:
        logger.error("affect.calibration: report write failed", exc_info=True)


def latest_report() -> dict | None:
    """Most recent reflection report, or None."""
    if not _REFLECTIONS_DIR.exists():
        return None
    try:
        files = sorted(_REFLECTIONS_DIR.glob("*.json"))
        if not files:
            return None
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except Exception:
        return None
