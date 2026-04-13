"""Discover external temporal rhythms from experiential data (Proposal §5.2).

Pure functions over event logs. Adapters supply the logs (interaction,
firecrawl, task) so this module is unit-testable without Mem0/PG.

Discovered rhythms are NOT declared. If Andrus changes his work
schedule, the system notices within 1-2 weeks. If TikTok shifts its
update cycle, Firecrawl pattern analysis detects it.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Rhythm:
    name: str                                       # human-readable
    kind: str                                       # 'andrus' | 'firecrawl' | 'task' | 'venture'
    frequency: str                                  # 'hourly' | 'daily' | 'weekly' | 'monthly'
    typical_hours: list = field(default_factory=list)  # e.g. [9, 10, 11, 14, 15]
    typical_weekdays: list = field(default_factory=list)  # 0=Mon ... 6=Sun
    sample_size: int = 0
    confidence: float = 0.0                         # 0-1; rises with sample_size


def _hour_of(ts: str) -> Optional[int]:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).hour
    except Exception:
        return None


def _weekday_of(ts: str) -> Optional[int]:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).weekday()
    except Exception:
        return None


def _modal_hours(timestamps: list, top_n: int = 6) -> list:
    counts: Counter = Counter()
    for ts in timestamps:
        h = _hour_of(ts)
        if h is not None:
            counts[h] += 1
    return [h for h, _ in counts.most_common(top_n)]


def _modal_weekdays(timestamps: list, top_n: int = 5) -> list:
    counts: Counter = Counter()
    for ts in timestamps:
        d = _weekday_of(ts)
        if d is not None:
            counts[d] += 1
    return [d for d, _ in counts.most_common(top_n)]


def _confidence_for(n: int) -> float:
    # Diminishing returns: 10 samples → 0.5, 50 samples → ~0.85, 200+ → ~1.0
    return round(min(1.0, n / 100.0 + 0.4 * (1.0 - 1.0 / max(1, n))), 4)


def discover_rhythms(
    *,
    interaction_log: Optional[list] = None,    # [{"timestamp": iso, ...}]
    firecrawl_log: Optional[list] = None,      # [{"timestamp": iso, "source": str}]
    task_log: Optional[list] = None,           # [{"timestamp": iso, "venture": str}]
    min_samples: int = 5,
) -> list:
    """Mine temporal patterns. Returns a list of Rhythm instances."""
    rhythms: list[Rhythm] = []

    if interaction_log and len(interaction_log) >= min_samples:
        ts = [e["timestamp"] for e in interaction_log if e.get("timestamp")]
        rhythms.append(Rhythm(
            name="andrus interaction rhythm",
            kind="andrus",
            frequency="daily",
            typical_hours=_modal_hours(ts),
            typical_weekdays=_modal_weekdays(ts),
            sample_size=len(ts),
            confidence=_confidence_for(len(ts)),
        ))

    if firecrawl_log:
        # Group by source for source-specific cycles
        by_source: dict[str, list] = {}
        for e in firecrawl_log:
            src = e.get("source") or "unknown"
            ts = e.get("timestamp")
            if ts:
                by_source.setdefault(src, []).append(ts)
        for src, ts_list in by_source.items():
            if len(ts_list) < min_samples:
                continue
            rhythms.append(Rhythm(
                name=f"{src} update cycle",
                kind="firecrawl",
                frequency="weekly",
                typical_hours=_modal_hours(ts_list),
                typical_weekdays=_modal_weekdays(ts_list),
                sample_size=len(ts_list),
                confidence=_confidence_for(len(ts_list)),
            ))

    if task_log:
        by_venture: dict[str, list] = {}
        for e in task_log:
            v = e.get("venture") or "unknown"
            ts = e.get("timestamp")
            if ts:
                by_venture.setdefault(v, []).append(ts)
        for v, ts_list in by_venture.items():
            if len(ts_list) < min_samples:
                continue
            rhythms.append(Rhythm(
                name=f"{v} task cluster",
                kind="venture",
                frequency="weekly",
                typical_weekdays=_modal_weekdays(ts_list),
                typical_hours=_modal_hours(ts_list),
                sample_size=len(ts_list),
                confidence=_confidence_for(len(ts_list)),
            ))

    return rhythms
