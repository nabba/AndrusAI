"""hot1_patterns_by_kind — 7-day HOT-1 meta-affect patterns grouped by
detector kind.

Background — 2026-05-23 SubIA audit Round 2 second-order finding. The
HOT-1 detectors started actually firing after commit 7560d067 fixed
the malformed welfare-audit parser. Patterns land in
``workspace/sentience/hot1_meta_affect.jsonl`` and are sampled by:

  * ``subia_observations`` (daily, top pattern_kind only — single line)
  * ``_gather_sentience_digest`` (weekly, trace-level kinds only —
    ``baseline_drift`` + ``attractor_lock``)

Neither breaks the rolling window down across all five detector kinds
(``temporal_cluster``, ``recurring_trigger``, ``sequence``,
``baseline_drift``, ``attractor_lock``). This section fills that gap.

Observational only — counts, never identities. Section auto-hides
when no patterns landed in the window.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

ID = "hot1-patterns-by-kind"
DISPLAY_NAME = "🧠 HOT-1 patterns by kind (7d)"
DESCRIPTION = (
    "Rolling 7-day breakdown of HOT-1 meta-affect patterns by detector "
    "kind (temporal_cluster / recurring_trigger / sequence / "
    "baseline_drift / attractor_lock). Complements the daily top-1 "
    "line in subia_observations and the weekly trace-level-only digest."
)

_WINDOW_DAYS = 7
_FETCH_LIMIT = 200


def gather() -> list[str]:
    try:
        from app.sentience_experiments.hot1_meta_affect import list_recent
    except Exception:
        logger.debug("hot1_patterns_by_kind: import failed", exc_info=True)
        return []

    try:
        patterns = list_recent(n=_FETCH_LIMIT) or []
    except Exception:
        logger.debug("hot1_patterns_by_kind: list_recent failed", exc_info=True)
        return []

    if not patterns:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=_WINDOW_DAYS)
    by_kind: Counter[str] = Counter()
    for row in patterns:
        ts = row.get("detected_at")
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
        kind = (row.get("pattern_kind") or "unknown").strip() or "unknown"
        by_kind[kind] += 1

    if not by_kind:
        return []

    lines = []
    for kind, count in by_kind.most_common():
        lines.append(f"  • {kind}: {count}")
    return lines
