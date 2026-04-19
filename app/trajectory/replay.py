"""
app.trajectory.replay — audit / debug view of a captured trajectory.

Given a `trajectory_id`, reconstructs the full signal surface from disk:
  * Trajectory sidecar (types.Trajectory) — steps, outcome_summary
  * AttributionRecord sidecar (if the analyzer ran)
  * Calibration entry (if the Observer ↔ Attribution loop was on)
  * Effectiveness rows (if tips were injected)

Two output shapes:

  replay(trajectory_id) -> dict
      Structured JSON-serialisable bundle for dashboards, the control
      plane API, or deep inspection from a shell.

  format_text(trajectory_id) -> str
      Human-readable text block — same fields, trimmed for terminal
      inspection. Safe to paste into bug reports.

No writes. No LLM calls. Pure I/O over the existing sidecar layout.

IMMUTABLE — infrastructure-level module.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── Structured bundle ────────────────────────────────────────────────

def replay(trajectory_id: str) -> dict:
    """Return a structured replay bundle for `trajectory_id`.

    Shape:
        {
          "trajectory_id": str,
          "trajectory": dict | None,      # Trajectory.to_dict()
          "attribution": dict | None,      # AttributionRecord.to_dict()
          "calibration": dict | None,      # calibration row dict or None
          "effectiveness_rows": list[dict] # zero or more rows
        }

    Never raises. Missing pieces land as None / [].
    """
    out = {
        "trajectory_id": trajectory_id or "",
        "trajectory": None,
        "attribution": None,
        "calibration": None,
        "effectiveness_rows": [],
    }
    if not trajectory_id:
        return out

    try:
        from app.trajectory.store import load_trajectory, load_attribution
        t = load_trajectory(trajectory_id)
        if t is not None:
            out["trajectory"] = t.to_dict()
        a = load_attribution(trajectory_id)
        if a is not None:
            out["attribution"] = a.to_dict()
    except Exception:
        logger.debug("replay: trajectory/attribution load failed", exc_info=True)

    # Calibration — scan the JSONL for the matching trajectory_id.
    try:
        from app.trajectory.calibration import _LOG_PATH, _tail, _WINDOW_SIZE
        for row in _tail(_WINDOW_SIZE):
            if row.get("trajectory_id") == trajectory_id:
                out["calibration"] = row
                break
    except Exception:
        logger.debug("replay: calibration lookup failed", exc_info=True)

    # Effectiveness — one row per (skill_id × trajectory_id). Surface all
    # rows matching this trajectory so the caller sees which tips were used.
    try:
        from app.trajectory.effectiveness import _tail as eff_tail, _WINDOW_SIZE as EFF_WINDOW
        for row in eff_tail(EFF_WINDOW):
            if row.get("trajectory_id") == trajectory_id:
                out["effectiveness_rows"].append(row)
    except Exception:
        logger.debug("replay: effectiveness lookup failed", exc_info=True)

    return out


# ── Text rendering ───────────────────────────────────────────────────

def format_text(trajectory_id: str) -> str:
    """Human-readable replay. Safe for terminals and bug reports."""
    b = replay(trajectory_id)
    if not b["trajectory"]:
        return f"[trajectory {trajectory_id!r} not found]"

    t = b["trajectory"]
    lines: list[str] = [
        f"Trajectory {t.get('trajectory_id', '?')}",
        f"  task_id:     {t.get('task_id', '')}",
        f"  crew:        {t.get('crew_name', '')}",
        f"  started_at:  {t.get('started_at', '')}",
        f"  ended_at:    {t.get('ended_at', '')}",
        f"  task:        {(t.get('task_description') or '')[:200]}",
    ]
    outcome = t.get("outcome_summary") or {}
    if outcome:
        lines.append("  outcome:")
        for k, v in outcome.items():
            lines.append(f"    {k}: {v}")
    injected = t.get("injected_skill_ids") or []
    if injected:
        lines.append(f"  injected_skill_ids ({len(injected)}):")
        for sid in injected:
            lines.append(f"    - {sid}")

    steps = t.get("steps") or []
    lines.append(f"\n  steps ({len(steps)}):")
    for s in steps:
        hdr = f"    [{s.get('step_idx')}] {s.get('phase')} role={s.get('agent_role')}"
        if s.get("planned_action"):
            hdr += f" action={s['planned_action'][:100]}"
        if s.get("tool_name"):
            hdr += f" tool={s['tool_name']}"
        if s.get("output_sample"):
            hdr += f" out={s['output_sample'][:80]}"
        if s.get("elapsed_ms"):
            hdr += f" {s['elapsed_ms']}ms"
        if s.get("observer_prediction"):
            pred = s["observer_prediction"]
            hdr += (
                f" OBSERVER={pred.get('predicted_failure_mode', '?')}"
                f"@{pred.get('confidence', 0.0):.0%}"
            )
        lines.append(hdr)

    a = b["attribution"]
    if a:
        lines.append("\n  attribution:")
        lines.append(f"    attribution_id: {a.get('attribution_id', '')}")
        lines.append(f"    verdict:        {a.get('verdict', '')}")
        lines.append(f"    failure_mode:   {a.get('failure_mode', '')}")
        lines.append(f"    attributed_step_idx: {a.get('attributed_step_idx', -1)}")
        lines.append(f"    confidence:     {a.get('confidence', 0.0):.2f}")
        lines.append(f"    suggested_tip_type: {a.get('suggested_tip_type', '')}")
        lines.append(f"    narrative:      {(a.get('narrative') or '')[:200]}")
    else:
        lines.append("\n  attribution: (none — run didn't meet the analysis gate)")

    c = b["calibration"]
    if c:
        lines.append("\n  observer_calibration_pair:")
        lines.append(f"    predicted_mode: {c.get('predicted_mode', '')}")
        lines.append(f"    predicted_confidence: {c.get('predicted_confidence', 0.0):.2f}")
        lines.append(f"    actual_mode:    {c.get('actual_mode', '')}")

    effs = b["effectiveness_rows"]
    if effs:
        lines.append(f"\n  effectiveness_rows ({len(effs)}):")
        for row in effs:
            lines.append(
                f"    - skill_id={row.get('skill_id')} "
                f"passed={row.get('passed_quality_gate')} "
                f"verdict={row.get('verdict', '')}"
            )

    return "\n".join(lines)
