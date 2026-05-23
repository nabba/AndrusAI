"""affect_summary — one-line summary of yesterday's affect trace.

Reads ``workspace/affect/trace.jsonl`` (canonical store for the
affective layer's viability / interoception / welfare signals).
Computes a coarse mood-vs-stress ratio for the last 24h and renders
it as a single line — the briefing doesn't surface the raw trace,
just whether yesterday was net positive or net stressed.

Composes with — does NOT replace — the welfare-breaching arbiter
that already filters whether to send the briefing at all. Soft fail
when affect data is unavailable.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

ID = "affect-summary"
DISPLAY_NAME = "🌡 Yesterday's mood"
DESCRIPTION = (
    "One-line affect summary from the last 24h of the affective trace. "
    "Net mood + stress signal — useful for noticing patterns."
)


def _trace_path() -> Path:
    from app.paths import WORKSPACE_ROOT
    return WORKSPACE_ROOT / "affect" / "trace.jsonl"


def gather() -> list[str]:
    p = _trace_path()
    if not p.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    pos = neg = stress = total = 0
    try:
        # Tail-read — the trace can be large, but rows are tiny so
        # we accept reading the whole file. Switch to seek-tail when
        # the trace exceeds a few MB.
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = row.get("ts") or row.get("timestamp")
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if dt < cutoff:
                continue
            total += 1
            valence = row.get("valence") or row.get("affect") or 0
            try:
                v = float(valence)
            except (TypeError, ValueError):
                v = 0.0
            if v > 0.2:
                pos += 1
            elif v < -0.2:
                neg += 1
            arousal = row.get("arousal") or row.get("stress") or 0
            try:
                a = float(arousal)
            except (TypeError, ValueError):
                a = 0.0
            if a > 0.6:
                stress += 1
    except Exception:
        logger.debug("affect_summary: read failed", exc_info=True)
        return []
    if total == 0:
        return []
    if pos > neg + 2:
        mood = "net positive"
    elif neg > pos + 2:
        mood = "net negative"
    else:
        mood = "balanced"
    stress_frac = stress / total if total else 0
    stress_part = ""
    if stress_frac > 0.3:
        stress_part = f", elevated stress ({stress_frac:.0%} of samples)"
    return [f"  • {mood} over {total} samples{stress_part}"]
