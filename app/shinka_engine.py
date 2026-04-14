"""
shinka_engine.py — ShinkaEvolve integration for AndrusAI.

Wraps ShinkaEvolve's ShinkaEvolveRunner as an alternative evolution engine
that can be selected via config.evolution_engine = "shinka".

ShinkaEvolve uses island-model MAP-Elites with LLM-generated patches
and EVOLVE-BLOCK markers for targeted code mutation. It provides:
  - Multi-island population with migration
  - UCB1 model selection across multiple LLMs
  - Diff, full-replacement, and crossover patch types
  - Novelty scoring via code embeddings
  - Async parallel evaluation

The integration:
  1. Reads AndrusAI's LLM configuration and maps to ShinkaEvolve model strings
  2. Points ShinkaEvolve at workspace/shinka/initial.py and evaluate.py
  3. Runs a bounded evolution session (num_generations from config)
  4. Extracts the best variant and applies it to the workspace
  5. Records results in the standard results_ledger

TIER_IMMUTABLE — this module is part of the evolution infrastructure.
"""

import json
import logging
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SHINKA_DIR = Path("/app/workspace/shinka")
SHINKA_RESULTS_DIR = Path("/app/workspace/shinka_results")
INITIAL_PY = SHINKA_DIR / "initial.py"
EVALUATE_PY = SHINKA_DIR / "evaluate.py"


@dataclass
class ShinkaResult:
    """Result from a ShinkaEvolve evolution session."""
    status: str  # "improved", "no_improvement", "error", "skipped"
    best_score: float = 0.0
    baseline_score: float = 0.0
    delta: float = 0.0
    generations_run: int = 0
    variants_evaluated: int = 0
    best_program_path: str = ""
    error: str = ""
    duration_seconds: float = 0.0


def _map_llm_models() -> list[str]:
    """Map AndrusAI's LLM configuration to ShinkaEvolve model strings.

    ShinkaEvolve supports: OpenAI, Anthropic, OpenRouter, and local models.
    We map our existing LLM cascade to ShinkaEvolve's format.
    """
    models = []

    # Check for Anthropic API key
    try:
        from app.config import get_settings
        settings = get_settings()
        if hasattr(settings, "anthropic_api_key"):
            key = settings.anthropic_api_key
            if hasattr(key, "get_secret_value"):
                key = key.get_secret_value()
            if key and len(key) > 10:
                models.append("us.anthropic.claude-sonnet-4-20250514-v1:0")
    except Exception:
        pass

    # Check for OpenRouter (budget tier)
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
    if openrouter_key:
        models.append("openrouter/deepseek/deepseek-chat-v3-0324")

    # Check for local Ollama
    ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    try:
        import requests
        resp = requests.get(f"{ollama_host}/api/tags", timeout=2)
        if resp.status_code == 200:
            tags = resp.json().get("models", [])
            for tag in tags:
                name = tag.get("name", "")
                if "coder" in name.lower() or "qwen" in name.lower():
                    models.append(f"local/{name}@{ollama_host}/v1")
                    break
    except Exception:
        pass

    # Fallback: if no models found, use OpenRouter with DeepSeek
    if not models:
        models.append("openrouter/deepseek/deepseek-chat-v3-0324")

    return models


def _get_embedding_model() -> str | None:
    """Get embedding model for novelty scoring.

    Returns None to disable embedding-based novelty (uses LLM-based instead).
    Embeddings require an OpenAI key which we may not have.
    """
    if os.environ.get("OPENAI_API_KEY"):
        return "text-embedding-3-small"
    return None


def run_shinka_session(
    num_generations: int = 20,
    num_islands: int = 2,
    max_eval_jobs: int = 2,
    max_proposal_jobs: int = 2,
) -> ShinkaResult:
    """Run a ShinkaEvolve evolution session.

    This is the main entry point called from evolution.py when
    config.evolution_engine == "shinka".

    Args:
        num_generations: How many generations to evolve.
        num_islands: Number of population islands.
        max_eval_jobs: Concurrent evaluation jobs.
        max_proposal_jobs: Concurrent LLM proposal jobs.

    Returns:
        ShinkaResult with status, scores, and best program path.
    """
    start = time.monotonic()

    # Gate: verify shinka files exist
    if not INITIAL_PY.exists():
        return ShinkaResult(
            status="skipped",
            error=f"Missing {INITIAL_PY}",
            duration_seconds=time.monotonic() - start,
        )
    if not EVALUATE_PY.exists():
        return ShinkaResult(
            status="skipped",
            error=f"Missing {EVALUATE_PY}",
            duration_seconds=time.monotonic() - start,
        )

    # Measure baseline from current initial.py
    baseline_score = _measure_baseline()

    try:
        from shinka.core import ShinkaEvolveRunner, EvolutionConfig
        from shinka.launch import LocalJobConfig
        from shinka.database import DatabaseConfig
    except ImportError as e:
        return ShinkaResult(
            status="error",
            error=f"ShinkaEvolve not installed: {e}",
            baseline_score=baseline_score,
            duration_seconds=time.monotonic() - start,
        )

    # Prepare results directory
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    results_dir = SHINKA_RESULTS_DIR / f"run_{ts}"
    results_dir.mkdir(parents=True, exist_ok=True)

    llm_models = _map_llm_models()
    embedding_model = _get_embedding_model()
    logger.info(f"shinka_engine: models={llm_models}, embedding={embedding_model}")

    try:
        evo_config = EvolutionConfig(
            num_generations=num_generations,
            init_program_path=str(INITIAL_PY),
            results_dir=str(results_dir),
            language="python",
            llm_models=llm_models,
            llm_dynamic_selection="ucb" if len(llm_models) > 1 else None,
            embedding_model=embedding_model,
            task_sys_msg=(
                "You are optimizing agent utility functions for AndrusAI. "
                "Improve tool selection accuracy, response formatting quality, "
                "and task routing correctness. The combined_score measures "
                "accuracy across test cases — higher is better."
            ),
            max_api_costs=5.0,  # USD budget cap per session
        )

        job_config = LocalJobConfig(
            eval_program_path=str(EVALUATE_PY),
        )

        db_config = DatabaseConfig(
            db_path=str(results_dir / "evolution_db.sqlite"),
            num_islands=num_islands,
            archive_size=max(20, num_generations),
            migration_interval=max(5, num_generations // 4),
        )

        runner = ShinkaEvolveRunner(
            evo_config=evo_config,
            job_config=job_config,
            db_config=db_config,
            max_evaluation_jobs=max_eval_jobs,
            max_proposal_jobs=max_proposal_jobs,
            verbose=True,
        )

        logger.info(f"shinka_engine: starting {num_generations} generations on {num_islands} islands")
        runner.run()

        # Extract results
        best_score, best_path = _extract_best_result(results_dir)
        delta = best_score - baseline_score

        duration = time.monotonic() - start

        if delta > 0 and best_path:
            # Apply the best variant to the workspace
            _apply_best_variant(best_path)
            logger.info(
                f"shinka_engine: improved! score={best_score:.4f} "
                f"(delta={delta:+.4f}, {duration:.0f}s)"
            )

            # Record in results ledger
            _record_result(baseline_score, best_score, delta, "keep")

            return ShinkaResult(
                status="improved",
                best_score=best_score,
                baseline_score=baseline_score,
                delta=delta,
                generations_run=num_generations,
                best_program_path=str(best_path),
                duration_seconds=duration,
            )
        else:
            logger.info(
                f"shinka_engine: no improvement (best={best_score:.4f}, "
                f"baseline={baseline_score:.4f}, {duration:.0f}s)"
            )
            _record_result(baseline_score, best_score, delta, "discard")

            return ShinkaResult(
                status="no_improvement",
                best_score=best_score,
                baseline_score=baseline_score,
                delta=delta,
                generations_run=num_generations,
                duration_seconds=duration,
            )

    except Exception as e:
        duration = time.monotonic() - start
        logger.error(f"shinka_engine: error: {e}")
        return ShinkaResult(
            status="error",
            error=str(e)[:500],
            baseline_score=baseline_score,
            duration_seconds=duration,
        )


def _measure_baseline() -> float:
    """Run the current initial.py through the test suite to get baseline score."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("initial", str(INITIAL_PY))
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            score, _ = mod.run_evaluation()
            return float(score)
    except Exception as e:
        logger.warning(f"shinka_engine: baseline measurement failed: {e}")
    return 0.0


def _extract_best_result(results_dir: Path) -> tuple[float, Path | None]:
    """Extract the best program and its score from ShinkaEvolve results.

    ShinkaEvolve stores results in an SQLite database. We also check
    for best_metrics.json as a simpler fallback.
    """
    best_score = 0.0
    best_path = None

    # Try SQLite database first
    try:
        from shinka.database import ProgramDatabase
        db_path = results_dir / "evolution_db.sqlite"
        if db_path.exists():
            db = ProgramDatabase.load(str(db_path))
            best = db.get_best_program()
            if best and best.fitness > best_score:
                best_score = best.fitness
                # Write best program to a file
                best_file = results_dir / "best_program.py"
                best_file.write_text(best.program_str)
                best_path = best_file
    except Exception as e:
        logger.debug(f"shinka_engine: DB extraction failed: {e}")

    # Fallback: check for best_metrics.json
    metrics_file = results_dir / "best_metrics.json"
    if metrics_file.exists():
        try:
            data = json.loads(metrics_file.read_text())
            score = data.get("combined_score", 0.0)
            if score > best_score:
                best_score = score
                program_file = results_dir / "best_program.py"
                if program_file.exists():
                    best_path = program_file
        except Exception:
            pass

    return best_score, best_path


def _apply_best_variant(best_path: Path) -> None:
    """Apply the best evolved variant back to workspace/shinka/initial.py.

    Creates a backup before overwriting.
    """
    try:
        # Backup current
        backup = INITIAL_PY.with_suffix(".py.bak")
        shutil.copy2(INITIAL_PY, backup)

        # Apply best variant
        shutil.copy2(best_path, INITIAL_PY)

        # Git commit
        from app.workspace_versioning import workspace_commit
        workspace_commit("evolution: ShinkaEvolve improved initial.py")

        logger.info(f"shinka_engine: applied best variant from {best_path}")
    except Exception as e:
        logger.error(f"shinka_engine: failed to apply variant: {e}")


def _record_result(
    baseline: float,
    after: float,
    delta: float,
    status: str,
) -> None:
    """Record the ShinkaEvolve result in the standard results ledger."""
    try:
        from app.results_ledger import record_experiment
        from app.experiment_runner import generate_experiment_id
        record_experiment(
            experiment_id=generate_experiment_id("shinka-evolve"),
            hypothesis="ShinkaEvolve island-model evolution of agent utilities",
            change_type="code",
            metric_before=baseline,
            metric_after=after,
            status=status,
            files_changed="workspace/shinka/initial.py",
            detail=f"ShinkaEvolve delta={delta:+.4f}",
        )
    except Exception as e:
        logger.warning(f"shinka_engine: ledger recording failed: {e}")
