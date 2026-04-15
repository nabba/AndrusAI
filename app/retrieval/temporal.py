"""
retrieval/temporal.py — Temporal freshness weighting for retrieval results.

Newer documents get a higher temporal score via exponential decay:

    temporal_score = 2 ** (-age_hours / half_life_hours)

With a 7-day half-life (default), a document ingested:
    - Today:        temporal_score ≈ 1.0
    - 1 week ago:   temporal_score ≈ 0.5
    - 2 weeks ago:  temporal_score ≈ 0.25
    - 1 month ago:  temporal_score ≈ 0.06

The temporal score is blended with the semantic score:
    final = (1 - weight) * semantic + weight * temporal

Backward-compatible: results without timestamps are returned unchanged.

IMMUTABLE — infrastructure-level module.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.retrieval import config as cfg

logger = logging.getLogger(__name__)


def _parse_timestamp(value: str) -> datetime | None:
    """Parse an ISO 8601 timestamp string to a timezone-aware datetime."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def apply_temporal_decay(
    results: list[dict],
    timestamp_field: str = "ingested_at",
    half_life_hours: float = cfg.TEMPORAL_HALF_LIFE_HOURS,
    weight: float = cfg.TEMPORAL_WEIGHT,
    now: datetime | None = None,
) -> list[dict]:
    """Add temporal freshness scoring to retrieval results.

    Each result dict should have a ``metadata`` sub-dict containing
    a timestamp field (ISO 8601 string).  If the timestamp is missing
    or unparseable, the result gets ``temporal_score = 0.5`` (neutral).

    The blended score is stored as ``blended_score``:
        ``(1 - weight) * semantic_score + weight * temporal_score``

    Parameters
    ----------
    results : list[dict]
        Retrieval results.  Each should have ``score`` (semantic) and
        ``metadata`` with a timestamp field.
    timestamp_field : str
        Key inside ``metadata`` holding the ISO timestamp.
    half_life_hours : float
        Half-life for exponential decay.
    weight : float
        Blend weight for temporal score (0 = ignore time, 1 = only time).
    now : datetime | None
        Override for current time (useful in tests).

    Returns
    -------
    list[dict]
        Same results with ``temporal_score`` and ``blended_score`` added,
        sorted by ``blended_score`` descending.
    """
    if not results:
        return []
    if weight <= 0:
        for r in results:
            r["temporal_score"] = 0.5
            r["blended_score"] = r.get("score", 0.0)
        return results

    _now = now or datetime.now(timezone.utc)

    for r in results:
        meta = r.get("metadata", {})
        ts_str = meta.get(timestamp_field, "") if isinstance(meta, dict) else ""
        ts = _parse_timestamp(ts_str)

        if ts is not None:
            age_hours = max(0.0, (_now - ts).total_seconds() / 3600.0)
            temporal = 2.0 ** (-age_hours / half_life_hours)
        else:
            temporal = 0.5  # Neutral — no timestamp means no freshness signal.

        semantic = r.get("score", 0.0)
        r["temporal_score"] = round(temporal, 4)
        r["blended_score"] = round((1.0 - weight) * semantic + weight * temporal, 4)

    results.sort(key=lambda r: r.get("blended_score", 0), reverse=True)
    return results
