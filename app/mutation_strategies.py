"""
mutation_strategies.py — Mutation type taxonomy for the AVO planner.

After 38 days of evolution, all 21 attempted code mutations were defensive
patterns (retry logic, validation wrappers, error handling). The system
discovered no architectural improvements, no refactoring, no capability
extensions, no optimizations. The proposal LLM was anchored to a narrow
mutation space.

This module defines six mutation strategies, each with sampling weight and
prompting guidance. Before each AVO planning cycle, a strategy is sampled
according to weights, and the strategy's prompt is injected into the
planner so it actively considers that direction.

Strategy weights live in workspace/meta/mutation_strategies.json and are
themselves evolvable by meta-evolution. Empirical success rates feed back
into adjusting the weights over time.

Six strategies:
  - DEFENSIVE     : error handling, validation, retries, timeouts
  - REFACTORING   : simplify, extract, dedupe (no behavior change)
  - CAPABILITY    : new tool / agent / endpoint / integration
  - ARCHITECTURAL : agent hierarchy, context flow, evaluation pipeline
  - REMOVAL       : delete unused, prune dead paths, simplify by subtraction
  - OPTIMIZATION  : caching, batching, parallelism, latency/cost reduction
"""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

STRATEGIES_PATH = Path("/app/workspace/meta/mutation_strategies.json")
STRATEGY_STATS_PATH = Path("/app/workspace/mutation_strategy_stats.json")


class MutationStrategy(str, Enum):
    DEFENSIVE = "defensive"
    REFACTORING = "refactoring"
    CAPABILITY = "capability"
    ARCHITECTURAL = "architectural"
    REMOVAL = "removal"
    OPTIMIZATION = "optimization"


@dataclass(frozen=True)
class StrategySpec:
    """One mutation strategy's configuration."""
    name: MutationStrategy
    weight: float                # Sampling probability (normalized at use)
    description: str             # 1-2 sentences
    examples: tuple[str, ...]    # Concrete example mutations of this type
    guidance: str                # When to use, when to avoid


# Hardcoded fallback if workspace/meta/ file is missing or invalid.
# Matches the current shape of mutation_strategies.json.
_DEFAULT_STRATEGIES: dict[MutationStrategy, StrategySpec] = {
    MutationStrategy.DEFENSIVE: StrategySpec(
        name=MutationStrategy.DEFENSIVE,
        weight=0.20,
        description="Error handling, validation wrappers, retry logic, timeouts.",
        examples=("wrap API call in try/except", "add retry with exponential backoff"),
        guidance="Use when there are recurring TRANSIENT errors.",
    ),
    MutationStrategy.REFACTORING: StrategySpec(
        name=MutationStrategy.REFACTORING,
        weight=0.20,
        description="Simplify, extract, dedupe. No behavior change.",
        examples=("extract repeated logic into helper", "combine similar functions"),
        guidance="Choose when same logic appears in 3+ places.",
    ),
    MutationStrategy.CAPABILITY: StrategySpec(
        name=MutationStrategy.CAPABILITY,
        weight=0.20,
        description="Add a new tool, agent skill, endpoint, or integration.",
        examples=("new web scraping tool", "new MCP server connection"),
        guidance="Use when system repeatedly fails because it lacks a capability.",
    ),
    MutationStrategy.ARCHITECTURAL: StrategySpec(
        name=MutationStrategy.ARCHITECTURAL,
        weight=0.15,
        description="Change agent hierarchy, context flow, or evaluation pipeline.",
        examples=("add reflection step", "split coder into design and implementation"),
        guidance="Highest leverage but highest risk.",
    ),
    MutationStrategy.REMOVAL: StrategySpec(
        name=MutationStrategy.REMOVAL,
        weight=0.10,
        description="Delete unused code, prune dead paths, simplify by subtraction.",
        examples=("remove unused imports", "drop deprecated config option"),
        guidance="Counterintuitive but powerful when system has accumulated cruft.",
    ),
    MutationStrategy.OPTIMIZATION: StrategySpec(
        name=MutationStrategy.OPTIMIZATION,
        weight=0.15,
        description="Caching, batching, parallelism, latency/cost reduction.",
        examples=("cache LLM responses", "parallelize independent agent calls"),
        guidance="Use when response_time or cost metrics show degradation.",
    ),
}


# ── Configuration loading ────────────────────────────────────────────────────

def load_strategies() -> dict[MutationStrategy, StrategySpec]:
    """Load strategy specs from workspace/meta/mutation_strategies.json.

    Falls back to _DEFAULT_STRATEGIES if missing or malformed.
    """
    if not STRATEGIES_PATH.exists():
        return _DEFAULT_STRATEGIES

    try:
        data = json.loads(STRATEGIES_PATH.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"mutation_strategies: load failed, using defaults: {e}")
        return _DEFAULT_STRATEGIES

    parsed: dict[MutationStrategy, StrategySpec] = {}
    for key, value in data.items():
        if key.startswith("_"):
            continue
        try:
            strategy = MutationStrategy(key)
            parsed[strategy] = StrategySpec(
                name=strategy,
                weight=float(value.get("weight", 0.0)),
                description=value.get("description", ""),
                examples=tuple(value.get("examples", [])),
                guidance=value.get("guidance", ""),
            )
        except (ValueError, KeyError, TypeError) as e:
            logger.debug(f"mutation_strategies: skipping invalid entry '{key}': {e}")

    # Ensure all six strategies exist (fall back to default for missing ones)
    for strategy in MutationStrategy:
        if strategy not in parsed:
            parsed[strategy] = _DEFAULT_STRATEGIES[strategy]

    return parsed


# ── Sampling ─────────────────────────────────────────────────────────────────

def select_strategy(seed: int | None = None) -> StrategySpec:
    """Sample one strategy according to configured weights.

    Args:
        seed: Optional RNG seed for deterministic sampling (for tests).

    Returns:
        The selected StrategySpec.
    """
    strategies = load_strategies()
    rng = random.Random(seed) if seed is not None else random.Random()

    items = list(strategies.values())
    weights = [max(0.0, s.weight) for s in items]
    total = sum(weights)
    if total <= 0:
        return items[0]  # Defensive fallback

    return rng.choices(items, weights=weights, k=1)[0]


# ── Prompting integration ────────────────────────────────────────────────────

def build_strategy_prompt_section(strategy: StrategySpec) -> str:
    """Build a prompt fragment to inject into AVO planning.

    The fragment tells the planner which mutation strategy to focus on,
    with examples and guidance. Designed to be appended to the existing
    AVO planning prompt without replacing it.
    """
    examples_text = "\n  - ".join(strategy.examples)
    return (
        f"\n## Strategy focus for this cycle: {strategy.name.value.upper()}\n"
        f"{strategy.description}\n\n"
        f"Examples of this strategy:\n  - {examples_text}\n\n"
        f"Guidance: {strategy.guidance}\n\n"
        f"While other approaches are valid, prefer this strategy unless there's a "
        f"clear reason to choose differently."
    )


# ── Success tracking (for adaptive weight tuning) ────────────────────────────

def update_strategy_success(strategy_name: str, succeeded: bool, delta: float = 0.0) -> None:
    """Record outcome of a strategy-driven mutation.

    Persisted to workspace/mutation_strategy_stats.json. Used by
    meta-evolution to tune weights over time: strategies with higher
    success rates can be promoted, lower-performing ones demoted.
    """
    try:
        stats = _load_stats()
        bucket = stats.setdefault(strategy_name, {
            "total": 0, "succeeded": 0, "cumulative_delta": 0.0,
        })
        bucket["total"] += 1
        if succeeded:
            bucket["succeeded"] += 1
            bucket["cumulative_delta"] += delta
        bucket["last_updated"] = time.time()
        _save_stats(stats)
    except Exception as e:
        logger.debug(f"mutation_strategies: stats update failed: {e}")


def _load_stats() -> dict:
    if not STRATEGY_STATS_PATH.exists():
        return {}
    try:
        return json.loads(STRATEGY_STATS_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_stats(stats: dict) -> None:
    try:
        STRATEGY_STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
        STRATEGY_STATS_PATH.write_text(json.dumps(stats, indent=2, default=str))
    except OSError:
        pass


def get_strategy_success_rates() -> dict[str, dict]:
    """Return per-strategy success rates for dashboard / meta-evolution."""
    stats = _load_stats()
    result = {}
    for name, bucket in stats.items():
        total = bucket.get("total", 0)
        succeeded = bucket.get("succeeded", 0)
        result[name] = {
            "total": total,
            "succeeded": succeeded,
            "success_rate": round(succeeded / max(1, total), 3),
            "cumulative_delta": round(bucket.get("cumulative_delta", 0.0), 4),
            "avg_delta_when_kept": round(
                bucket.get("cumulative_delta", 0.0) / max(1, succeeded), 4
            ),
        }
    return result
