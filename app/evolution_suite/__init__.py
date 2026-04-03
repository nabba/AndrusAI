"""
evolution_suite — Unified access to all evolution subsystems.

Provides a single import point for the 7 evolution-related modules
that were previously flat at app/ root level.

Usage:
    from app.evolution_suite import run_evolution_session, IslandEvolution
    from app.evolution_suite import EvolutionArchive, MapElitesGrid
    from app.evolution_suite import parse_prompt, validate_modification
    from app.evolution_suite import get_controller
    from app.evolution_suite import CascadeEvaluator
"""

# Core autoresearch loop
from app.evolution import run_evolution_session

# Island-based population evolution
from app.island_evolution import (
    IslandEvolution, Island, Individual,
    run_island_evolution_cycle,
)

# Parallel evolution with diverse archive
from app.parallel_evolution import (
    EvolutionArchive, ArchiveEntry, ParallelEvolutionRunner,
    run_parallel_evolution_cycle, get_runner,
)

# EVOLVE-BLOCK / FREEZE-BLOCK markers
from app.evolve_blocks import (
    parse_prompt, validate_modification, has_evolve_blocks,
    get_frozen_hash, annotate_prompt, extract_evolvable_content,
)

# Adaptive ensemble + scheduling
from app.adaptive_ensemble import (
    AdaptiveEvolutionController, WeightedEnsemble,
    PlateauScheduler, ExponentialScheduler, CosineScheduler,
    get_controller,
)

# MAP-Elites quality-diversity grid
from app.map_elites import MAPElitesDB

# Cascade evaluator
from app.cascade_evaluator import CascadeEvaluator
