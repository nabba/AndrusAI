"""Deterministic bias detectors over aggregated behavioural data.

Pure functions. The miner aggregates raw history; these functions
turn aggregates into named findings.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass


@dataclass
class Finding:
    kind: str          # 'attentional' | 'prediction' | 'avoidance' | 'divergence'
    name: str
    detail: str
    quantitative: dict


def detect_attentional_bias(scene_history: list, normalize_by: dict | None = None) -> list:
    """Identify ventures/topics over- or under-represented in focal scenes.

    `scene_history` is a list of dicts {venture/topic: count}.
    `normalize_by` lets callers normalise by commitment count etc.
    Returns Finding list (kind='attentional').
    """
    if not scene_history:
        return []
    totals: Counter = Counter()
    for entry in scene_history:
        totals.update(entry)
    if not totals:
        return []
    mean = sum(totals.values()) / len(totals)
    findings: list = []
    for key, count in totals.items():
        norm = (normalize_by or {}).get(key, 1) or 1
        ratio = (count / norm) / max(0.01, mean)
        if ratio >= 1.5:
            findings.append(Finding(
                kind="attentional",
                name=f"over_attention:{key}",
                detail=f"{key} receives {ratio:.1f}x normalized focal attention",
                quantitative={"ratio": round(ratio, 3), "count": count},
            ))
        elif ratio <= 0.5 and count >= 1:
            findings.append(Finding(
                kind="attentional",
                name=f"under_attention:{key}",
                detail=f"{key} receives only {ratio:.1f}x normalized focal attention",
                quantitative={"ratio": round(ratio, 3), "count": count},
            ))
    return findings


def detect_prediction_bias(prediction_errors: list) -> list:
    """Each entry: {domain, predicted, actual, error}. Returns Finding list."""
    if not prediction_errors:
        return []
    by_domain: dict[str, list] = {}
    for e in prediction_errors:
        by_domain.setdefault(e.get("domain", "unknown"), []).append(e)
    findings: list = []
    for domain, errs in by_domain.items():
        if len(errs) < 5:
            continue
        signed = [float(e.get("error", 0.0)) for e in errs]
        mean_signed = sum(signed) / len(signed)
        if abs(mean_signed) >= 0.1:
            direction = "overconfident" if mean_signed > 0 else "underconfident"
            findings.append(Finding(
                kind="prediction",
                name=f"systematic_bias:{domain}",
                detail=f"{domain}: {abs(mean_signed)*100:.0f}% {direction}",
                quantitative={"mean_signed_error": round(mean_signed, 3),
                              "n": len(errs)},
            ))
    return findings


def detect_avoidance(restoration_queue_log: list, action_log: list) -> list:
    """Find homeostatic variables consistently in restoration queue but
    never actioned. Each entry of restoration_queue_log is a list of
    variable names that needed restoration at some tick; action_log is
    [{variable_addressed}].
    """
    if not restoration_queue_log:
        return []
    needed: Counter = Counter()
    for queue in restoration_queue_log:
        needed.update(queue)
    addressed = Counter(a.get("variable_addressed", "") for a in action_log or [])
    findings: list = []
    for var, n_needed in needed.items():
        if n_needed >= 3 and addressed.get(var, 0) == 0:
            findings.append(Finding(
                kind="avoidance",
                name=f"never_addressed:{var}",
                detail=f"{var} appeared in restoration queue {n_needed}x but was never addressed",
                quantitative={"times_needed": n_needed, "times_addressed": 0},
            ))
    return findings


def detect_affect_action_divergence(affect_log: list, action_log: list) -> list:
    """Compare reported affect vs. actual behaviour.

    affect_log entries: {affect, exploration_followed: bool}
    Returns Finding list when correlation < 0.7.
    """
    if not affect_log:
        return []
    by_affect: dict[str, list] = {}
    for e in affect_log:
        by_affect.setdefault(e.get("affect", "unknown"), []).append(
            bool(e.get("exploration_followed", False))
        )
    findings: list = []
    for affect, outcomes in by_affect.items():
        if len(outcomes) < 5:
            continue
        rate = sum(outcomes) / len(outcomes)
        if rate < 0.7:
            findings.append(Finding(
                kind="divergence",
                name=f"affect_action_divergence:{affect}",
                detail=f"reported '{affect}' followed by congruent action only {rate*100:.0f}% of cases",
                quantitative={"correlation_rate": round(rate, 3),
                              "n": len(outcomes)},
            ))
    return findings
