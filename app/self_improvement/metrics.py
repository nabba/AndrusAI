"""
app.self_improvement.metrics — observability for the self-improvement loop.

Phase 6 of the overhaul. Dashboard-facing summary functions:

    pipeline_funnel()          →  gaps detected → drafted → integrated → used
    topic_diversity()          →  Shannon entropy over KB topic clusters
    novelty_histogram()         →  distribution of novelty decisions (rolling)
    health_summary()           →  one-call aggregated view for the dashboard

All functions are read-only aggregations over existing stores. No writes,
no side-effects. Safe to call from any thread or endpoint.
"""

from __future__ import annotations

import logging
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


def pipeline_funnel() -> dict:
    """Counts at each stage of the gap→skill pipeline.

    Returns {
        gaps_open, gaps_scheduled,
        gaps_resolved_existing, gaps_resolved_new, gaps_rejected,
        skills_active, skills_superseded, skills_archived,
        consolidation_proposals, auto_merged,
        zombies_30d, zombies_60d,
    }
    """
    out = {}

    # Gap counts by status
    try:
        from app.self_improvement.store import _get_collection
        from app.self_improvement.types import GapStatus
        col = _get_collection()
        if col is not None:
            for status in GapStatus:
                try:
                    r = col.get(where={"status": status.value}, limit=2000)
                    out[f"gaps_{status.value}"] = len(r.get("ids", []))
                except Exception:
                    out[f"gaps_{status.value}"] = 0
    except Exception:
        pass

    # Skill counts by status
    try:
        from app.self_improvement.integrator import list_records
        for st in ("active", "superseded", "archived"):
            out[f"skills_{st}"] = len(list_records(status=st, limit=5000))
    except Exception:
        out["skills_active"] = 0

    # Consolidation proposal count
    try:
        from app.self_improvement.consolidator import list_proposals
        props = list_proposals(limit=200)
        out["consolidation_proposals"] = len(props)
        out["auto_merged"] = sum(1 for p in props if p.get("auto_merged"))
    except Exception:
        out["consolidation_proposals"] = 0
        out["auto_merged"] = 0

    # Zombie counts (from Evaluator's usage distribution)
    try:
        from app.self_improvement.evaluator import usage_distribution
        d = usage_distribution()
        out["zombies_30d"] = d.get("zombies_30d", 0)
        out["zombies_60d"] = d.get("zombies_60d", 0)
    except Exception:
        pass

    return out


def topic_diversity(kb: Optional[str] = None, n_bins: int = 0) -> dict:
    """Topic diversity measured as Shannon entropy over skill clusters.

    Since we don't have pre-built clusters, we approximate with topic
    bigrams (first two tokens of the topic name). A healthy system has
    high entropy across many bigrams; a monoculture has a single dominant
    bigram (e.g. 'rapid_ecological' in the pre-overhaul state).

    Returns {
        entropy, normalized_entropy (0..1), total,
        top_clusters = [(bigram, count, fraction), ...]
    }
    """
    try:
        from app.self_improvement.integrator import list_records
        records = list_records(kb=kb, status="active", limit=2000)
    except Exception:
        records = []

    if not records:
        return {
            "entropy": 0.0, "normalized_entropy": 0.0,
            "total": 0, "top_clusters": [],
        }

    bigrams: Counter = Counter()
    for r in records:
        # Handle both slug-style ("rapid_ecological_impact") and
        # sentence-style ("Key learnings about rapid ecological...") topics.
        import re as _re
        toks = _re.split(r"[\s_\-]+", r.topic.lower().strip())
        # Skip filler words to get more meaningful bigrams
        _STOP = {"key", "the", "a", "an", "of", "for", "and", "in", "on",
                 "to", "with", "about", "from", "by", "is", "are",
                 "include", "includes", "involves", "learnings", "findings",
                 "summary", "strategies", "techniques", "concepts"}
        meaningful = [t for t in toks if t and t not in _STOP]
        key = "_".join(meaningful[:2]) if len(meaningful) >= 2 else (meaningful[0] if meaningful else "?")
        bigrams[key] += 1

    total = sum(bigrams.values())
    probs = [c / total for c in bigrams.values()]
    entropy = -sum(p * math.log2(p) for p in probs if p > 0)
    # Max entropy given this many categories (uniform distribution)
    max_ent = math.log2(len(bigrams)) if len(bigrams) > 1 else 1.0
    normalized = entropy / max_ent if max_ent > 0 else 0.0

    top = bigrams.most_common(10)
    top_clusters = [
        {"cluster": b, "count": c, "fraction": round(c / total, 3)}
        for b, c in top
    ]

    return {
        "entropy": round(entropy, 3),
        "normalized_entropy": round(normalized, 3),
        "total": total,
        "top_clusters": top_clusters,
    }


def novelty_histogram(sample_size: int = 50) -> dict:
    """Rolling distribution of Novelty Gate decisions.

    Samples `sample_size` recent proposals and runs their content through
    the Novelty Gate. Slow enough that we keep the sample bounded. Useful
    for calibrating the novelty thresholds over time.

    Lightweight fallback: if novelty results aren't stored (they aren't
    today — they're computed on the fly), this returns a decision-distribution
    summary based on a batch re-check.
    """
    out = {
        "sample_size": sample_size,
        "by_decision": {"covered": 0, "overlap": 0, "adjacent": 0, "novel": 0},
        "mean_distance": 0.0,
    }
    try:
        from app.self_improvement.integrator import list_records
        from app.self_improvement.novelty import novelty_report
    except Exception:
        return out

    records = list_records(status="active", limit=sample_size)
    if not records:
        return out

    distances = []
    for rec in records:
        # Use topic for a cheap check; content-level would be slower
        try:
            rep = novelty_report(rec.topic)
            out["by_decision"][rep.decision.value] = (
                out["by_decision"].get(rep.decision.value, 0) + 1
            )
            distances.append(rep.nearest_distance)
        except Exception:
            continue

    if distances:
        out["mean_distance"] = round(sum(distances) / len(distances), 3)
    out["actual_sample"] = len(records)
    return out


def health_summary() -> dict:
    """Single-call aggregated view for the dashboard.

    Joins pipeline_funnel + topic_diversity + competence_summary + baseline
    info into one dict. Designed to be cheap enough to serve from an HTTP
    endpoint on the dashboard polling interval.
    """
    out = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        out["funnel"] = pipeline_funnel()
    except Exception:
        out["funnel"] = {}
    try:
        out["diversity"] = topic_diversity()
    except Exception:
        out["diversity"] = {}
    try:
        from app.subia.self.competence_map import get_competence_summary
        out["competence"] = get_competence_summary()
    except Exception:
        out["competence"] = {}
    try:
        from app.map_elites_wiring import get_baseline_report
        out["map_elites_baselines"] = get_baseline_report()
    except Exception:
        out["map_elites_baselines"] = {}
    return out
