"""
kb_metadata.py — Append affect tags to experiential and tensions KBs at episode end.

Wires the affective layer into the existing KB v2 stores so that:

    - Experiential KB receives a brief affect-tagged record of every
      meaningful episode (valence arc, peak arousal, terminal attractor).
    - Tensions KB receives an entry whenever a single viability variable
      stays out of band for a sustained episode — those are the "growth
      edges" of the system.

Threshold gating: trivial episodes (no significant affect intensity, no
out-of-band variables) are not appended. This keeps the KBs from filling
up with noise.

Fallback: if ChromaDB stores are unavailable (e.g., bench/test env), the
data is appended to /app/workspace/affect/episode_affect_tags.jsonl so it
isn't lost.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.affect.schemas import AffectState, utc_now_iso

logger = logging.getLogger(__name__)

_FALLBACK_FILE = Path("/app/workspace/affect/episode_affect_tags.jsonl")

# Threshold for "memorable" — episodes below this don't earn a KB entry.
_MIN_INTENSITY = 0.20
_OUT_OF_BAND_TOLERANCE = 0.25


def _summarize_episode(ctx: Any, terminal_affect: AffectState | None) -> dict:
    """Build a structured summary that's small but informative."""
    task = (ctx.task_description or "")[:200]
    crew = ctx.metadata.get("crew", "")
    success = not ctx.abort and not ctx.errors

    a = terminal_affect.to_dict() if terminal_affect else {}
    v_frame = ctx.metadata.get("_viability_frame") or {}
    out_of_band = list(v_frame.get("out_of_band", []))[:5]

    return {
        "ts": utc_now_iso(),
        "task_id": ctx.get("task_id", "") or ctx.metadata.get("task_id", ""),
        "agent_id": ctx.agent_id,
        "crew": crew,
        "task_preview": task,
        "success": success,
        "errors": [str(e)[:200] for e in ctx.errors[:3]],
        "terminal_affect": a,
        "out_of_band_variables": out_of_band,
        "total_error": v_frame.get("total_error", 0.0),
    }


def _append_fallback(record: dict) -> None:
    try:
        _FALLBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _FALLBACK_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception:
        logger.debug("affect.kb_metadata: fallback write failed", exc_info=True)


def tag_episode_with_affect(ctx: Any, terminal_affect: AffectState | None) -> None:
    """Append affect-tagged entries to experiential and (when warranted) tensions KBs."""
    if terminal_affect is None:
        return

    record = _summarize_episode(ctx, terminal_affect)

    # Always write to fallback log — durable, append-only.
    _append_fallback(record)

    # Memorable threshold check: must have either non-trivial affect intensity
    # OR at least one out-of-band variable. Otherwise it's mundane background.
    intensity = abs(terminal_affect.valence) + terminal_affect.arousal
    has_out_of_band = bool(record["out_of_band_variables"])
    if intensity < _MIN_INTENSITY and not has_out_of_band:
        return

    # ── Experiential KB: brief narrative + structured metadata ───────────
    try:
        from app.experiential.vectorstore import get_store as _get_exp
        exp = _get_exp()
        if exp is not None:
            text = _format_experiential_text(record, terminal_affect)
            metadata = {
                "agent": str(record["agent_id"])[:80],
                "crew": str(record["crew"])[:80],
                "outcome": "success" if record["success"] else "failure",
                "valence": float(terminal_affect.valence),
                "arousal": float(terminal_affect.arousal),
                "controllability": float(terminal_affect.controllability),
                "attractor": str(terminal_affect.attractor)[:40],
                "total_error": float(record["total_error"]),
                "out_of_band": ",".join(record["out_of_band_variables"])[:200],
                "ts": record["ts"],
            }
            exp.add_entry(text=text, metadata=metadata)
    except Exception:
        logger.debug("affect.kb_metadata: experiential write failed", exc_info=True)

    # ── Tensions KB: only when a single dimension is out-of-band ─────────
    if has_out_of_band:
        try:
            from app.tensions.vectorstore import get_store as _get_ten
            ten = _get_ten()
            if ten is not None:
                tension_text = _format_tension_text(record, terminal_affect)
                tension_meta = {
                    "tension_name": f"viability:{','.join(record['out_of_band_variables'])}",
                    "agents_involved": str(record["agent_id"])[:80],
                    "valence_at_close": float(terminal_affect.valence),
                    "arousal_at_close": float(terminal_affect.arousal),
                    "attractor": str(terminal_affect.attractor)[:40],
                    "ts": record["ts"],
                }
                ten.add_tension(text=tension_text, metadata=tension_meta)
        except Exception:
            logger.debug("affect.kb_metadata: tensions write failed", exc_info=True)


def _format_experiential_text(record: dict, affect: AffectState) -> str:
    parts = [
        f"Episode {record.get('task_id', '?')} on crew '{record['crew']}'.",
        f"Task: {record['task_preview']}",
        f"Outcome: {'success' if record['success'] else 'failure'}.",
        f"Affect: attractor={affect.attractor}, "
        f"V={affect.valence:.2f} A={affect.arousal:.2f} C={affect.controllability:.2f}.",
    ]
    if record["out_of_band_variables"]:
        parts.append(f"Out-of-band: {', '.join(record['out_of_band_variables'])}.")
    if record["errors"]:
        parts.append(f"Errors: {' | '.join(record['errors'])[:300]}")
    return " ".join(parts)


def _format_tension_text(record: dict, affect: AffectState) -> str:
    return (
        f"Tension recorded at episode close: viability variables "
        f"{', '.join(record['out_of_band_variables'])} were out of healthy band. "
        f"Terminal attractor: {affect.attractor} (V={affect.valence:.2f}, "
        f"A={affect.arousal:.2f}, C={affect.controllability:.2f}). "
        f"Task preview: {record['task_preview']}"
    )
