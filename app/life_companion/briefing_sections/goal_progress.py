"""goal_progress — current goals + a one-line activity summary.

Reads ``kernel.self_state.current_goals`` (the autonomous-goal queue
fed by ``affect.goal_emitter`` per the consciousness roadmap §3.G1).
Surfaces up to 3 active goals plus their last touchpoint. Useful for
keeping long-horizon goals in view between the weekly briefing
cadence."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ID = "goal-progress"
DISPLAY_NAME = "🎯 Current goals"
DESCRIPTION = (
    "Up to 3 active goals from kernel.self_state.current_goals plus the "
    "most recent activity touchpoint. Keeps long-horizon goals visible."
)


def gather() -> list[str]:
    try:
        from app.kernel import self_state
    except Exception:
        logger.debug("goal_progress: kernel.self_state import failed", exc_info=True)
        return []
    goals: list = []
    try:
        if hasattr(self_state, "load_state"):
            state = self_state.load_state() or {}
            goals = state.get("current_goals") or []
        elif hasattr(self_state, "current_goals"):
            goals = self_state.current_goals() or []
    except Exception:
        logger.debug("goal_progress: state read failed", exc_info=True)
        return []
    if not goals:
        return []
    out: list[str] = []
    for g in goals[:3]:
        if isinstance(g, dict):
            text = g.get("description") or g.get("text") or g.get("goal") or ""
            last_iso = g.get("updated_at") or g.get("last_progress_at") or ""
        else:
            text = str(g)
            last_iso = ""
        text = (text or "").strip()[:120]
        if not text:
            continue
        age = ""
        if last_iso:
            try:
                dt = datetime.fromisoformat(str(last_iso).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                days = (datetime.now(timezone.utc) - dt).days
                age = f" · last touched {days}d ago"
            except ValueError:
                age = ""
        out.append(f"  • {text}{age}")
    return out
