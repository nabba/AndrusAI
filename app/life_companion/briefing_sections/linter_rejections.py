"""linter_rejections — PhenomenalLanguageLinter HARD_FAIL rejections from
thread-closure distillation.

Background — 2026-05-23 SubIA audit Round 2 second-order finding. The
linter wired into ``_llm_distill`` in ``app/threads/approaches.py`` was
silently rejecting first-person phenomenal-state output and falling
back to the deterministic body builder. ``app.threads.linter_telemetry``
gave that rejection a footprint (JSONL append + running summary); this
section gives the operator a daily window into it.

Observational only — no Signal alerts, no actions. Soft-fails throughout.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

ID = "linter-rejections"
DISPLAY_NAME = "🚫 Phenomenal-language rejections"
DESCRIPTION = (
    "Rolling 7-day count of PhenomenalLanguageLinter HARD_FAIL rejections "
    "from thread-closure distillation, plus top patterns. Surfaces a "
    "previously-silent fallback path. Hides when nothing has ever been "
    "rejected."
)

_WINDOW_DAYS = 7
_TOP_N_PATTERNS = 3


def _rejection_log_path() -> Path:
    from app.paths import WORKSPACE_ROOT
    return WORKSPACE_ROOT / "threads" / "linter_rejections.jsonl"


def gather() -> list[str]:
    try:
        from app.threads.linter_telemetry import summary
        s = summary()
    except Exception:
        logger.debug("linter_rejections: summary() failed", exc_info=True)
        return []

    lifetime = int(s.get("total_rejections", 0) or 0)
    if lifetime == 0:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=_WINDOW_DAYS)
    window_count = 0
    pattern_counter: Counter[str] = Counter()

    path = _rejection_log_path()
    if path.exists():
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = row.get("ts")
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
                window_count += 1
                pat = (row.get("sample_pattern") or "").strip()
                if pat:
                    pattern_counter[pat] += 1
        except OSError:
            logger.debug("linter_rejections: jsonl read failed", exc_info=True)

    lines = [
        f"  • {window_count} in last {_WINDOW_DAYS}d ({lifetime} lifetime)"
    ]
    for pattern, count in pattern_counter.most_common(_TOP_N_PATTERNS):
        truncated = pattern if len(pattern) <= 80 else pattern[:77] + "..."
        lines.append(f"  • {truncated} ({count}×)")
    return lines
