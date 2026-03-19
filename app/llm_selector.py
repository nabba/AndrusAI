"""
llm_selector.py — Task-aware model selection.

Analyzes the task to determine type, then picks the best model using:
1. Catalog strengths (static knowledge of what each model is good at)
2. Benchmark history (actual measured performance per model per task)
3. Available system resources (don't pick a 20GB model on a low-RAM system)
"""

import logging
import re

from app.llm_catalog import get_candidates, get_smallest_model, get_ram_requirement, _TASK_ALIASES
from app.llm_benchmarks import get_scores

logger = logging.getLogger(__name__)

# Keywords that map to task types — used to auto-detect task from hint text
_KEYWORD_PATTERNS: list[tuple[str, str]] = [
    (r"\b(debug|traceback|error|fix\s+bug|stacktrace)\b", "debugging"),
    (r"\b(architect|design|plan|system\s+design|review)\b", "architecture"),
    (r"\b(code|implement|function|class|module|script|program)\b", "coding"),
    (r"\b(research|search|find|learn|investigate|analyze)\b", "research"),
    (r"\b(write|summarize|document|report|explain|describe)\b", "writing"),
    (r"\b(reason|think|logic|proof|math)\b", "reasoning"),
]


def detect_task_type(role: str, task_hint: str = "") -> str:
    """
    Detect the task type from role + optional hint text.
    Returns a canonical task type string.
    """
    # First: check hint text for keywords
    if task_hint:
        hint_lower = task_hint.lower()
        for pattern, task_type in _KEYWORD_PATTERNS:
            if re.search(pattern, hint_lower):
                return task_type

    # Second: map role to task type
    role_map = {
        "coding": "coding",
        "architecture": "architecture",
        "research": "research",
        "writing": "writing",
        "default": "general",
    }
    return role_map.get(role, "general")


def select_model(
    role: str,
    task_hint: str = "",
    max_ram_gb: float = 48.0,
) -> str:
    """
    Pick the best available model for this role and task.

    Algorithm:
    1. Detect task type from role + hint
    2. Get catalog candidates ranked by strength
    3. Boost/adjust scores with benchmark data
    4. Filter by RAM constraint
    5. Return top model (or smallest fallback)
    """
    task_type = detect_task_type(role, task_hint)

    # Catalog candidates: [(model, catalog_score)]
    candidates = get_candidates(task_type)

    # Benchmark adjustments: model → measured_score
    bench_scores = get_scores(task_type)

    # Check which models are actually available in the fleet volume
    available_models = set()
    try:
        from app.ollama_fleet import get_available_models
        available_models = set(get_available_models())
    except Exception:
        pass

    # Merge: weighted average of catalog score and benchmark score
    # Strongly prefer models that are already downloaded (no pull delay)
    BENCH_WEIGHT = 0.4
    AVAILABLE_BOOST = 0.3  # boost for models already in the volume
    merged = []
    for model, catalog_score in candidates:
        ram = get_ram_requirement(model)
        if ram > max_ram_gb:
            continue

        bench = bench_scores.get(model)
        if bench is not None:
            score = (1 - BENCH_WEIGHT) * catalog_score + BENCH_WEIGHT * bench
        else:
            score = catalog_score

        # Boost already-available models so we don't pick undownloaded ones
        if available_models and model in available_models:
            score += AVAILABLE_BOOST

        merged.append((model, score))

    merged.sort(key=lambda x: -x[1])

    if merged:
        best_model, best_score = merged[0]
        logger.info(
            f"llm_selector: task={task_type} role={role} → {best_model} "
            f"(score={best_score:.2f})"
        )
        return best_model

    # Fallback
    fallback = get_smallest_model()
    logger.warning(f"llm_selector: no suitable model, falling back to {fallback}")
    return fallback
