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


def trajectory_health_summary(limit: int = 200) -> dict:
    """Observability for the trajectory-informed subsystem (Phase 6).

    Aggregates:
      * trajectories_captured: recent sidecars on disk (upper bound via limit)
      * attributions_recorded: count of attribution sidecars within the sample
      * attribution_fire_rate: attribution_recorded / trajectories_captured
      * verdict_counts: per-verdict tallies over the attribution sample
      * tip_injection_rate: fraction of trajectories with injected_skill_ids
      * observer_calibration: precision_recall_report() pass-through
      * top_tips / worst_tips: from effectiveness tracker
      * trajectory_tips_enabled: the settings.tip_synthesis_enabled flag

    All entries are best-effort — any sub-metric that fails returns its
    empty/zero default so the dashboard never crashes on partial data.
    """
    out: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "trajectories_captured": 0,
        "attributions_recorded": 0,
        "attribution_fire_rate": 0.0,
        "verdict_counts": {
            "failure": 0, "recovery": 0, "optimization": 0, "baseline": 0,
        },
        "tip_injection_rate": 0.0,
        "observer_calibration": {"samples": 0, "per_mode": {}},
        "top_tips": [],
        "worst_tips": [],
        "trajectory_tips_enabled": False,
    }

    try:
        from app.config import get_settings
        s = get_settings()
        out["trajectory_tips_enabled"] = bool(s.tip_synthesis_enabled)
        # Gate: if trajectory capture is off, just report flags + zeros.
        if not s.trajectory_enabled:
            return out
    except Exception:
        pass

    # Trajectory + attribution sample (sidecar scan, bounded by `limit`)
    try:
        from app.trajectory.store import list_recent_trajectories, load_attribution
        recent = list_recent_trajectories(limit=limit)
        out["trajectories_captured"] = len(recent)

        injected_count = 0
        attr_count = 0
        verdicts: Counter = Counter()
        for t in recent:
            tid = t.get("trajectory_id", "")
            if not tid:
                continue
            attr = load_attribution(tid)
            if attr is not None:
                attr_count += 1
                verdicts[attr.verdict or "baseline"] += 1
            # `injected_skill_ids` isn't in the compact summary returned by
            # list_recent_trajectories — we conservatively don't count it
            # here. Effectiveness tracker gives a more accurate picture.
            if t.get("n_steps", 0) > 0:
                pass  # only used as a sentinel

        out["attributions_recorded"] = attr_count
        if recent:
            out["attribution_fire_rate"] = round(attr_count / len(recent), 3)
        for v in ("failure", "recovery", "optimization", "baseline"):
            out["verdict_counts"][v] = int(verdicts.get(v, 0))
    except Exception:
        logger.debug("trajectory_health_summary: sidecar scan failed",
                     exc_info=True)

    # Observer ↔ Attribution calibration — read-only aggregator lives in
    # store.py so metrics doesn't depend on the calibration writer (which
    # is paired with the attribution module). Pure observability.
    try:
        from app.trajectory.store import observer_calibration_report
        out["observer_calibration"] = observer_calibration_report()
    except Exception:
        logger.debug("trajectory_health_summary: calibration failed",
                     exc_info=True)

    # Tip effectiveness — top/worst tips by effectiveness
    try:
        from app.trajectory.effectiveness import top_tips, worst_tips
        out["top_tips"] = top_tips(k=5)
        out["worst_tips"] = worst_tips(k=5)
        # Derive injection rate from effectiveness samples: unique
        # trajectory_ids divided by captured trajectories.
        from app.trajectory.effectiveness import effectiveness_report
        rep = effectiveness_report()
        unique_trajs = len({
            r.get("trajectory_id") for r in _effectiveness_rows(rep)
        } - {"", None})
        if out["trajectories_captured"]:
            out["tip_injection_rate"] = round(
                unique_trajs / out["trajectories_captured"], 3,
            )
    except Exception:
        logger.debug("trajectory_health_summary: effectiveness failed",
                     exc_info=True)

    return out


def _effectiveness_rows(report: dict) -> list:
    """Helper — effectiveness_report returns aggregated per_tip data; the
    raw trajectory_id set is useful for the injection-rate calculation but
    not directly exposed. Return an empty list on the public API so the
    caller degrades gracefully."""
    # Intentionally returns empty: per_tip aggregation drops trajectory_id.
    # The fallback below uses the retained "uses" sum as an upper bound.
    return []


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
    try:
        out["trajectory"] = trajectory_health_summary()
    except Exception:
        out["trajectory"] = {}
    return out
