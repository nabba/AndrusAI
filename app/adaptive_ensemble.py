"""
adaptive_ensemble.py — Phase-dependent weighted LLM ensemble + adaptive scheduling.

Wraps the existing 4-tier LLM cascade with probabilistic model selection
that shifts based on the current evolution phase.

Phases:
    exploration  — 70% local, 20% budget, 10% mid (diverse, cheap)
    exploitation — 50% premium, 30% mid, 20% budget (quality-focused)
    meta_prompt  — 60% mid, 40% premium (reasoning-heavy, infrequent)
    evaluation   — 80% local, 20% budget (simple judge tasks, throughput)

Adaptive scheduling:
    PlateauScheduler — detects when fitness plateaus and increases exploration
    ExponentialScheduler — decays exploration rate over time

Inspired by CodeEvolve's weighted ensemble and adaptive scheduling.

IMMUTABLE — infrastructure-level module.
"""

from __future__ import annotations

import logging
import math
import random
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── IMMUTABLE: Phase-dependent model weights ─────────────────────────────────

# Model tiers matching existing llm_catalog.py / llm_selector.py
MODEL_TIERS = {
    "local":   {"weight_key": "local",   "cost_per_1k": 0.0},
    "budget":  {"weight_key": "budget",  "cost_per_1k": 0.001},
    "mid":     {"weight_key": "mid",     "cost_per_1k": 0.01},
    "premium": {"weight_key": "premium", "cost_per_1k": 0.10},
}

# Phase → tier probability distribution
PHASE_WEIGHTS: dict[str, dict[str, float]] = {
    "exploration": {
        "local": 0.70, "budget": 0.20, "mid": 0.10, "premium": 0.00,
    },
    "exploitation": {
        "local": 0.00, "budget": 0.20, "mid": 0.30, "premium": 0.50,
    },
    "meta_prompting": {
        "local": 0.00, "budget": 0.00, "mid": 0.60, "premium": 0.40,
    },
    "evaluation": {
        "local": 0.80, "budget": 0.20, "mid": 0.00, "premium": 0.00,
    },
}

# ── Weighted Ensemble ─────────────────────────────────────────────────────────

class WeightedEnsemble:
    """Probabilistically selects a model tier based on phase weights."""

    def __init__(self, phase: str = "exploration"):
        self._phase = phase
        self._weights = PHASE_WEIGHTS.get(phase, PHASE_WEIGHTS["exploration"])
        self._call_count = 0
        self._tier_counts: dict[str, int] = {t: 0 for t in MODEL_TIERS}

    @property
    def phase(self) -> str:
        return self._phase

    def set_phase(self, phase: str) -> None:
        if phase in PHASE_WEIGHTS:
            self._phase = phase
            self._weights = PHASE_WEIGHTS[phase]
            logger.info(f"adaptive_ensemble: phase → {phase} "
                        f"(weights: {self._weights})")

    def select_tier(self) -> str:
        """Probabilistically select a model tier."""
        tiers = list(self._weights.keys())
        weights = [self._weights[t] for t in tiers]

        # Handle all-zero edge case
        if sum(weights) == 0:
            return "budget"

        selected = random.choices(tiers, weights=weights, k=1)[0]
        self._call_count += 1
        self._tier_counts[selected] = self._tier_counts.get(selected, 0) + 1
        return selected

    def create_llm(self, **kwargs):
        """Select a tier and create an LLM via existing llm_factory."""
        tier = self.select_tier()
        return self._create_for_tier(tier, **kwargs)

    def _create_for_tier(self, tier: str, **kwargs):
        """Create an LLM for a specific tier using existing factory."""
        from app.llm_factory import create_specialist_llm

        role_map = {
            "local": "self_improve",
            "budget": "self_improve",
            "mid": "coding",
            "premium": "coding",
        }
        role = kwargs.pop("role", role_map.get(tier, "self_improve"))
        return create_specialist_llm(role=role, **kwargs)

    def get_stats(self) -> dict:
        return {
            "phase": self._phase,
            "weights": dict(self._weights),
            "call_count": self._call_count,
            "tier_distribution": dict(self._tier_counts),
        }

# ── Adaptive Schedulers ──────────────────────────────────────────────────────

class PlateauScheduler:
    """Detects fitness plateaus and adjusts exploration rate.

    When fitness hasn't improved for `patience` epochs, increases
    exploration rate to escape local optima.
    """

    def __init__(
        self,
        initial_rate: float = 0.3,
        min_rate: float = 0.1,
        max_rate: float = 0.8,
        patience: int = 5,
        boost_factor: float = 1.5,
        decay_factor: float = 0.95,
    ):
        self._rate = initial_rate
        self._min = min_rate
        self._max = max_rate
        self._patience = patience
        self._boost = boost_factor
        self._decay = decay_factor
        self._best_fitness = 0.0
        self._epochs_since_improvement = 0

    @property
    def exploration_rate(self) -> float:
        return self._rate

    def step(self, current_fitness: float) -> float:
        """Update exploration rate based on fitness progress.

        Returns: current exploration rate
        """
        if current_fitness > self._best_fitness + 0.01:
            # Improvement detected — decay exploration (exploit more)
            self._best_fitness = current_fitness
            self._epochs_since_improvement = 0
            self._rate = max(self._min, self._rate * self._decay)
        else:
            # No improvement — accumulate patience
            self._epochs_since_improvement += 1
            if self._epochs_since_improvement >= self._patience:
                # Plateau detected — boost exploration
                self._rate = min(self._max, self._rate * self._boost)
                logger.info(f"adaptive_ensemble: plateau detected, "
                            f"exploration rate → {self._rate:.2f}")

        return self._rate

    def reset(self) -> None:
        self._best_fitness = 0.0
        self._epochs_since_improvement = 0

class ExponentialScheduler:
    """Exponentially decays exploration rate over time.

    Good for convergent evolution where you want to explore widely
    at first, then narrow down.
    """

    def __init__(
        self,
        initial_rate: float = 0.7,
        min_rate: float = 0.1,
        decay_per_epoch: float = 0.95,
    ):
        self._initial = initial_rate
        self._rate = initial_rate
        self._min = min_rate
        self._decay = decay_per_epoch

    @property
    def exploration_rate(self) -> float:
        return self._rate

    def step(self, current_fitness: float = 0.0) -> float:
        self._rate = max(self._min, self._rate * self._decay)
        return self._rate

    def reset(self) -> None:
        self._rate = self._initial

class CosineScheduler:
    """Cosine annealing exploration rate with warm restarts.

    Cycles between high and low exploration rates, allowing periodic
    re-exploration even late in the search.
    """

    def __init__(
        self,
        max_rate: float = 0.7,
        min_rate: float = 0.1,
        cycle_epochs: int = 10,
    ):
        self._max = max_rate
        self._min = min_rate
        self._cycle = cycle_epochs
        self._epoch = 0

    @property
    def exploration_rate(self) -> float:
        progress = (self._epoch % self._cycle) / self._cycle
        return self._min + (self._max - self._min) * 0.5 * (1 + math.cos(math.pi * progress))

    def step(self, current_fitness: float = 0.0) -> float:
        self._epoch += 1
        return self.exploration_rate

    def reset(self) -> None:
        self._epoch = 0

# ── Combined controller ──────────────────────────────────────────────────────

class AdaptiveEvolutionController:
    """Combines weighted ensemble + adaptive scheduling for evolution control.

    Manages the explore/exploit balance across evolution sessions.
    """

    def __init__(self, scheduler_type: str = "plateau"):
        self._ensemble = WeightedEnsemble(phase="exploration")

        if scheduler_type == "plateau":
            self._scheduler = PlateauScheduler()
        elif scheduler_type == "exponential":
            self._scheduler = ExponentialScheduler()
        elif scheduler_type == "cosine":
            self._scheduler = CosineScheduler()
        else:
            self._scheduler = PlateauScheduler()

        self._epoch = 0

    @property
    def exploration_rate(self) -> float:
        return self._scheduler.exploration_rate

    @property
    def ensemble(self) -> WeightedEnsemble:
        return self._ensemble

    def step(self, fitness: float) -> dict:
        """Advance one epoch. Updates exploration rate and ensemble phase.

        Returns: {exploration_rate, phase, tier_selected}
        """
        self._epoch += 1
        rate = self._scheduler.step(fitness)

        # Update ensemble phase based on exploration rate
        if rate > 0.5:
            self._ensemble.set_phase("exploration")
        elif rate > 0.2:
            # Mixed phase — use exploitation with some exploration
            self._ensemble.set_phase("exploitation")
        else:
            self._ensemble.set_phase("exploitation")

        return {
            "epoch": self._epoch,
            "exploration_rate": rate,
            "phase": self._ensemble.phase,
        }

    def select_mutation_strategy(self) -> str:
        """Select mutation strategy based on current exploration rate."""
        rate = self.exploration_rate
        if random.random() < rate:
            # Exploration: prefer meta-prompting and random mutations
            return random.choice(["meta_prompt", "random"])
        else:
            # Exploitation: prefer inspiration crossover and depth refinement
            return random.choice(["inspiration", "depth_exploit"])

    def create_llm(self, **kwargs):
        """Create an LLM via the phase-dependent ensemble."""
        return self._ensemble.create_llm(**kwargs)

    def get_stats(self) -> dict:
        return {
            "epoch": self._epoch,
            "exploration_rate": self.exploration_rate,
            "ensemble": self._ensemble.get_stats(),
        }

    def format_report(self) -> str:
        stats = self.get_stats()
        return (
            f"🎛️ Adaptive Evolution Controller\n"
            f"   Epoch: {stats['epoch']}\n"
            f"   Exploration rate: {stats['exploration_rate']:.2f}\n"
            f"   Phase: {stats['ensemble']['phase']}\n"
            f"   Tier distribution: {stats['ensemble']['tier_distribution']}"
        )

# ── Module-level singleton ───────────────────────────────────────────────────

_controller: AdaptiveEvolutionController | None = None

def get_controller() -> AdaptiveEvolutionController:
    global _controller
    if _controller is None:
        _controller = AdaptiveEvolutionController(scheduler_type="plateau")
    return _controller
