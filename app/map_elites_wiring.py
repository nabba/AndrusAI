"""
map_elites_wiring.py — Translate real crew outcomes into MAP-Elites grid writes.

This module is the bridge between observed agent behavior and the quality-diversity
grid in app/map_elites.py. It is intentionally the only place where outcome signals
are composed into a fitness score and strategy signature — the grid itself stays
pure infrastructure.

Three responsibilities:

    1. compute_fitness(outcome) -> float
       Multi-objective composite from real observable signals. Not a placeholder.

    2. build_strategy_signature(crew_name, task, result, backstory) -> str
       The string passed to extract_features(). Must carry behavioral signal —
       reflects HOW the agent approached the task (backstory + task shape),
       not the topic phrase alone.

    3. record_crew_outcome(...)
       Called from the POST crew telemetry hook. Composes signature + fitness
       into a StrategyEntry and writes it to the per-role MAP-Elites database.

Design invariants:

    - Fitness composition weights are config-level constants, not agent-modifiable
      (CLAUDE.md safety invariant: evaluation criteria live at infrastructure level).
    - A failure still writes to the grid. Low-fitness entries in void cells
      are useful exploration signal. Only uncaught exceptions skip writes.
    - Writes are best-effort: any failure in this module must not break crew
      execution. All public functions wrap in try/except and return silently.

IMMUTABLE — infrastructure-level module.
"""

from __future__ import annotations

import hashlib
import logging
import statistics
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ── IMMUTABLE: fitness composition weights ───────────────────────────────────
# Sum to 1.0. Tuning requires a config change, not an agent decision.

FITNESS_WEIGHTS = {
    "quality_gate": 0.35,   # passed the heuristic output check
    "confidence":   0.25,   # heuristic confidence (high/medium/low → 1.0/0.6/0.2)
    "completeness": 0.15,   # complete/partial/failed → 1.0/0.5/0.0
    "latency":      0.15,   # faster-than-expected for difficulty is rewarded
    "retry_cost":   0.10,   # fewer reflexion retries is better
}

# Confidence enum → score
_CONFIDENCE_SCORE = {"high": 1.0, "medium": 0.6, "low": 0.2}
_COMPLETENESS_SCORE = {"complete": 1.0, "partial": 0.5, "failed": 0.0}

# Cold-start fallback: seconds-per-difficulty before any observations exist.
# Replaced per-role by the learned baseline once we have ≥10 successful samples.
_COLD_START_LATENCY_S_PER_DIFFICULTY = 12.0

# Per-role rolling baseline tracker.  We keep up to N (latency, difficulty)
# pairs per role and recompute median latency-per-difficulty on demand.
# Only successful runs contribute — failure latencies would skew the baseline
# upward and make every subsequent run look fast.
_BASELINE_WINDOW = 50
_BASELINE_MIN_SAMPLES = 10
_baseline_history: dict[str, deque] = {}
_baseline_lock = threading.Lock()
_baseline_cache: dict[str, float] = {}    # role → seconds-per-difficulty
_baseline_cache_seq: dict[str, int] = {}  # role → samples-at-last-recompute


def _record_baseline_sample(role: str, duration_s: float, difficulty: int,
                            success: bool) -> None:
    """Push a (latency_per_difficulty) sample into the rolling window."""
    if not success or duration_s <= 0 or difficulty <= 0:
        return
    per_d = duration_s / max(difficulty, 1)
    if per_d <= 0 or per_d > 600:  # sanity bound
        return
    with _baseline_lock:
        buf = _baseline_history.setdefault(role, deque(maxlen=_BASELINE_WINDOW))
        buf.append(per_d)


def _get_role_latency_baseline(role: str) -> float:
    """Median latency-per-difficulty for this role.

    Uses a small cache keyed on sample count so we recompute only when new
    samples arrive, not on every fitness call.
    """
    with _baseline_lock:
        buf = _baseline_history.get(role)
        if not buf or len(buf) < _BASELINE_MIN_SAMPLES:
            return _COLD_START_LATENCY_S_PER_DIFFICULTY
        n = len(buf)
        if _baseline_cache_seq.get(role) == n:
            return _baseline_cache[role]
        baseline = float(statistics.median(buf))
        # Clamp to a reasonable band so a single anomalous regime doesn't
        # swing the baseline so far that prior entries' fitness inverts.
        baseline = max(3.0, min(baseline, 120.0))
        _baseline_cache[role] = baseline
        _baseline_cache_seq[role] = n
        return baseline


@dataclass
class CrewOutcome:
    """Structured outcome of a single crew execution.

    Composed by the orchestrator's post-telemetry hook from signals already
    present there (no new instrumentation needed).
    """
    crew_name: str
    task_description: str
    result: str
    backstory_snippet: str = ""  # first ~500 chars of the agent's backstory
    difficulty: int = 3
    duration_s: float = 0.0
    confidence: str = "medium"           # high | medium | low
    completeness: str = "partial"        # complete | partial | failed
    passed_quality_gate: bool = True
    has_result: bool = True
    is_failure_pattern: bool = False
    retries: int = 0                     # reflexion retry count
    reflexion_exhausted: bool = False


# ── Fitness ──────────────────────────────────────────────────────────────────

def compute_fitness(outcome: CrewOutcome) -> float:
    """Multi-objective composite fitness in [0.0, 1.0].

    Rewards: high confidence, complete output, passing quality gate, reasonable
    latency, low retry count. Punishes failure patterns and timeouts.
    """
    # Quality gate — binary but high-weight
    s_quality = 1.0 if (outcome.passed_quality_gate and outcome.has_result
                        and not outcome.is_failure_pattern) else 0.0

    # Confidence
    s_confidence = _CONFIDENCE_SCORE.get(outcome.confidence, 0.5)

    # Completeness
    s_completeness = _COMPLETENESS_SCORE.get(outcome.completeness, 0.5)

    # Latency — difficulty-aware target.  Uses learned per-role baseline once
    # ≥10 successful samples exist, otherwise the cold-start constant.
    # Score is 1.0 at or under expected, decays linearly to 0 at 4× expected.
    per_d = _get_role_latency_baseline(outcome.crew_name)
    expected_s = max(per_d * max(outcome.difficulty, 1), 6.0)
    if outcome.duration_s <= 0:
        s_latency = 0.5  # no signal — don't penalize
    elif outcome.duration_s <= expected_s:
        s_latency = 1.0
    else:
        overshoot = (outcome.duration_s - expected_s) / (3.0 * expected_s)
        s_latency = max(0.0, 1.0 - overshoot)

    # Retry cost — 0 retries → 1.0, 1 → 0.6, 2 → 0.3, 3+ → 0.0
    s_retry = max(0.0, 1.0 - 0.4 * outcome.retries)

    fitness = (
        FITNESS_WEIGHTS["quality_gate"]  * s_quality
        + FITNESS_WEIGHTS["confidence"]    * s_confidence
        + FITNESS_WEIGHTS["completeness"]  * s_completeness
        + FITNESS_WEIGHTS["latency"]       * s_latency
        + FITNESS_WEIGHTS["retry_cost"]    * s_retry
    )
    # Reflexion exhausted is a catastrophic signal — cap fitness
    if outcome.reflexion_exhausted:
        fitness = min(fitness, 0.25)

    return round(max(0.0, min(1.0, fitness)), 4)


# ── Strategy signature ───────────────────────────────────────────────────────

def build_strategy_signature(outcome: CrewOutcome) -> str:
    """Compose the string used for feature extraction.

    The goal: produce variance along (complexity, cost_efficiency, specialization)
    that actually reflects how this agent approached this task — not just the
    topic phrase.

    Recipe:
        - Role backstory (stable per role, gives domain/specialization signal)
        - Task description (task-level complexity/cost markers)
        - A short result summary (proxies the depth/breadth of the response)

    Length is capped to keep feature extraction fast. The backstory_snippet is
    supplied by the caller (truncated at call site) so this module stays pure.
    """
    parts = []
    if outcome.backstory_snippet:
        parts.append(outcome.backstory_snippet[:500])
    if outcome.task_description:
        parts.append(outcome.task_description[:800])
    if outcome.result:
        # Result summary — first few lines, enough for structural features
        result_head = "\n".join(outcome.result.strip().splitlines()[:15])
        parts.append(result_head[:600])
    return "\n\n".join(parts)


# ── Record ───────────────────────────────────────────────────────────────────

def record_crew_outcome(outcome: CrewOutcome) -> bool:
    """Write a StrategyEntry to the MAP-Elites grid for this crew's role.

    Returns True on successful write, False on any error (never raises).
    """
    try:
        from app.map_elites import (
            get_db, StrategyEntry, Artifact, extract_features,
        )

        db = get_db(outcome.crew_name)
        signature = build_strategy_signature(outcome)
        if not signature.strip():
            return False

        fitness = compute_fitness(outcome)
        features = extract_features(signature)

        # Deterministic ID from role + signature — lets the grid dedup exact
        # re-runs of the same strategy within a generation.
        strategy_id = hashlib.sha256(
            f"{outcome.crew_name}:{signature}".encode()
        ).hexdigest()[:12]

        entry = StrategyEntry(
            strategy_id=strategy_id,
            role=outcome.crew_name,
            prompt_content=signature,
            fitness_score=fitness,
            feature_vector=features,
            generation=db.generation,
            mutation_type="observed_execution",
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        # Feed the per-role rolling latency baseline so the next fitness call
        # gets a self-calibrated expected duration. Only successful runs count
        # — failures would inflate the baseline and inversely reward slow runs.
        _record_baseline_sample(
            outcome.crew_name, outcome.duration_s, outcome.difficulty,
            success=(outcome.passed_quality_gate and outcome.has_result),
        )

        replaced = db.add_strategy(entry, island_id=0)

        # Record an artifact so generation-to-generation feedback has real data
        db.record_artifact(Artifact(
            generation=db.generation,
            success=outcome.passed_quality_gate and outcome.has_result,
            score=fitness,
            execution_time_ms=outcome.duration_s * 1000.0,
            stage_reached="full" if outcome.completeness == "complete" else
                          ("smoke" if outcome.completeness == "partial" else "format"),
            failure_stage="" if outcome.passed_quality_gate else "quality_gate",
            llm_feedback=outcome.result[:200] if outcome.result else "",
        ))

        # Advance generation every 10 writes per role — keeps artifact history meaningful
        # without persisting on every single call (too much disk I/O).
        if _should_step_generation(outcome.crew_name):
            db.step_generation()
            db.persist()

        logger.debug(
            f"map_elites[{outcome.crew_name}]: write "
            f"fitness={fitness:.3f} features={features} replaced={replaced}"
        )
        return True

    except Exception as exc:
        logger.debug(f"map_elites_wiring: record failed: {exc}", exc_info=True)
        return False


# Light generation stepper — one step per N writes per role.
_WRITES_PER_GENERATION = 10
_write_counters: dict[str, int] = {}


def _should_step_generation(role: str) -> bool:
    n = _write_counters.get(role, 0) + 1
    _write_counters[role] = n
    return n % _WRITES_PER_GENERATION == 0


def get_baseline_report() -> dict:
    """Snapshot of per-role latency baselines.  Used by health dashboards
    and the homeostasis layer to expose self-calibration state."""
    out = {}
    with _baseline_lock:
        for role, buf in _baseline_history.items():
            out[role] = {
                "samples": len(buf),
                "baseline_s_per_difficulty": (
                    _baseline_cache.get(role)
                    if len(buf) >= _BASELINE_MIN_SAMPLES
                    else _COLD_START_LATENCY_S_PER_DIFFICULTY
                ),
                "is_learned": len(buf) >= _BASELINE_MIN_SAMPLES,
            }
    return out
