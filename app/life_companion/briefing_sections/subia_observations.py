"""subia_observations — top-1 finding from each of the 4 sentience modules.

Compact daily counterpart to the existing weekly sentience digest
(``_gather_sentience_digest`` in daily_briefing.py). Keeps the four
Q5 modules visible on a daily cadence without spamming the weekly's
verbosity — one line per module max, only when something happened
in the last 24h.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

ID = "subia-observations"
DISPLAY_NAME = "🔬 Sentience observations (24h)"
DESCRIPTION = (
    "Daily 1-liner from each of the 4 sentience modules (AE-2 / HOT-1 / "
    "HOT-4 / RPT-1). Complements the weekly digest with a per-day pulse."
)


def gather() -> list[str]:
    lines: list[str] = []
    # AE-2 — recent high-density associations
    try:
        from app.sentience_experiments.ae2_causal_credit import list_recent
        ae = list_recent(n=2) or []
        if ae:
            top = ae[0]
            ratio = float(top.get("outcome_density_ratio", 0))
            if ratio >= 3.0:
                lines.append(
                    f"  • AE-2: top rare-event association {ratio:.1f}× density"
                )
    except Exception:
        pass
    # HOT-1 — recent affect-pattern detection
    try:
        from app.sentience_experiments.hot1_meta_affect import list_recent
        hot1 = list_recent(n=2) or []
        if hot1:
            top = hot1[0]
            kind = top.get("pattern_kind") or "pattern"
            lines.append(f"  • HOT-1: {kind} detected")
    except Exception:
        pass
    # HOT-4 — flagged steps in the last 24h
    try:
        from app.sentience_experiments.hot4_metacog_monitor import list_recent_flagged
        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        flagged = list_recent_flagged(n=5, since_iso=since) or []
        if flagged:
            lines.append(
                f"  • HOT-4: {len(flagged)} unusual reasoning-chain step"
                f"{'s' if len(flagged) != 1 else ''} (24h)"
            )
    except Exception:
        pass
    # RPT-1 — calibration state delta from last briefing
    try:
        from app.sentience_experiments.rpt1_self_calibration import load_calibration_state
        state = load_calibration_state() or {}
        reports = state.get("reports") or {}
        if reports:
            n_kinds = len(reports)
            best = min(reports.values(), key=lambda r: float(r.get("brier_score", 1.0)))
            lines.append(
                f"  • RPT-1: {n_kinds} kind"
                f"{'s' if n_kinds != 1 else ''} calibrated, "
                f"best Brier={float(best.get('brier_score', 0)):.3f}"
            )
    except Exception:
        pass
    return lines
