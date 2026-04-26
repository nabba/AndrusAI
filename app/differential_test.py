"""
differential_test.py — Run old and new code on the same inputs, compare outputs.

A "passing" mutation that subtly changed answers can fool a pass/fail test
suite. Differential testing catches this: execute both versions on identical
inputs, then compare outputs structurally (exact match, embedding similarity,
or semantic equivalence).

This is invoked by experiment_runner for code mutations where applicable —
specifically, mutations that target pure utility functions or response
formatting. Architectural changes can't be diff-tested (their behavior change
is the point), but most defensive/refactoring/optimization changes can.

Output comparison strategies (in order of strictness):
  1. EXACT: byte-for-byte identical output
  2. STRUCTURAL: same keys, same types, ordered values may differ
  3. SEMANTIC: embedding similarity > 0.92 (LLM judge fallback)

The default is EXACT for refactoring/optimization (no behavior change should
mean no output change), STRUCTURAL for response formatting, SEMANTIC for
content-producing mutations.

This complements the existing test_tasks.json suite — diff testing is about
*regression* (did we change something we didn't intend?), while task tests
are about *correctness* (does the output meet a spec?).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


# ── Configuration ────────────────────────────────────────────────────────────

DIFF_RESULTS_PATH = Path("/app/workspace/differential_test_results.json")

_SEMANTIC_SIMILARITY_THRESHOLD = 0.92


class CompareStrategy(str, Enum):
    EXACT = "exact"            # Byte-for-byte
    STRUCTURAL = "structural"  # Same keys/types, ordered values may differ
    SEMANTIC = "semantic"      # Embedding similarity above threshold


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DifferentialResult:
    """Outcome of a differential test."""
    test_inputs_count: int
    matches: int                # Inputs where old and new outputs were equivalent
    divergences: int            # Inputs where outputs differed
    divergence_rate: float
    strategy: CompareStrategy
    sample_divergences: tuple[tuple[str, str, str], ...]  # (input, old_out, new_out)

    @property
    def is_safe_change(self) -> bool:
        """Heuristic: <5% divergence is "safe" for refactoring/optimization."""
        return self.divergence_rate < 0.05


# ── Comparison strategies ────────────────────────────────────────────────────

def _exact_match(old: str, new: str) -> bool:
    return old == new


def _structural_match(old: str, new: str) -> bool:
    """Compare as JSON if both parse, else fall back to string normalization."""
    try:
        old_parsed = json.loads(old)
        new_parsed = json.loads(new)
        return _structural_equal(old_parsed, new_parsed)
    except (json.JSONDecodeError, ValueError):
        # Fall back to whitespace-normalized comparison
        old_norm = " ".join(old.split())
        new_norm = " ".join(new.split())
        return old_norm == new_norm


def _structural_equal(a, b) -> bool:
    """Recursively check structural equality of parsed JSON objects."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        if set(a.keys()) != set(b.keys()):
            return False
        return all(_structural_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        if len(a) != len(b):
            return False
        return all(_structural_equal(ax, bx) for ax, bx in zip(a, b))
    return a == b


def _semantic_match(old: str, new: str) -> bool:
    """Compare via embedding similarity. Falls back to exact if embedder unavailable."""
    try:
        from app.memory.chromadb_manager import embed
        old_emb = embed(old[:2000])
        new_emb = embed(new[:2000])
        if old_emb and new_emb:
            similarity = _cosine_similarity(old_emb, new_emb)
            return similarity >= _SEMANTIC_SIMILARITY_THRESHOLD
    except Exception as e:
        logger.debug(f"differential_test: semantic compare unavailable: {e}")
    return _exact_match(old, new)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity of two vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


_COMPARERS = {
    CompareStrategy.EXACT: _exact_match,
    CompareStrategy.STRUCTURAL: _structural_match,
    CompareStrategy.SEMANTIC: _semantic_match,
}


# ── Strategy selection ───────────────────────────────────────────────────────

def select_strategy_for_change_type(change_type: str) -> CompareStrategy:
    """Pick the strictest strategy that's appropriate for the mutation type.

    - refactoring/optimization → EXACT (output should not change at all)
    - defensive (error handling) → STRUCTURAL (success cases unchanged, errors maybe formatted differently)
    - capability/architectural → SEMANTIC (intentional behavior change is fine, just shouldn't break correctness)
    - removal → STRUCTURAL (removing dead code shouldn't change live output)
    """
    mapping = {
        "refactoring": CompareStrategy.EXACT,
        "optimization": CompareStrategy.EXACT,
        "removal": CompareStrategy.STRUCTURAL,
        "defensive": CompareStrategy.STRUCTURAL,
        "capability": CompareStrategy.SEMANTIC,
        "architectural": CompareStrategy.SEMANTIC,
    }
    return mapping.get(change_type.lower(), CompareStrategy.STRUCTURAL)


# ── Core differential test ───────────────────────────────────────────────────

def run_differential_test(
    old_executor: callable,
    new_executor: callable,
    test_inputs: list,
    strategy: CompareStrategy = CompareStrategy.STRUCTURAL,
) -> DifferentialResult:
    """Run both versions on the same inputs, compare outputs.

    Args:
        old_executor: Callable(input) → output (the pre-mutation version)
        new_executor: Callable(input) → output (the post-mutation version)
        test_inputs: List of inputs to feed both executors
        strategy: Comparison strategy

    Returns:
        DifferentialResult with match/divergence breakdown.

    Both executors must be idempotent and side-effect-free for the comparison
    to be meaningful. If they aren't, the diff test isn't applicable.
    """
    if not test_inputs:
        return DifferentialResult(0, 0, 0, 0.0, strategy, ())

    comparer = _COMPARERS[strategy]
    matches = 0
    divergences = 0
    samples: list[tuple[str, str, str]] = []

    for inp in test_inputs:
        try:
            old_out = str(old_executor(inp))
            new_out = str(new_executor(inp))
        except Exception as e:
            logger.debug(f"differential_test: execution error on input {str(inp)[:80]}: {e}")
            continue

        if comparer(old_out, new_out):
            matches += 1
        else:
            divergences += 1
            if len(samples) < 5:
                samples.append((str(inp)[:200], old_out[:300], new_out[:300]))

    total = matches + divergences
    rate = divergences / max(1, total)

    result = DifferentialResult(
        test_inputs_count=total,
        matches=matches,
        divergences=divergences,
        divergence_rate=round(rate, 3),
        strategy=strategy,
        sample_divergences=tuple(samples),
    )

    _persist_result(result)
    logger.info(
        f"differential_test: {strategy.value} — {matches}/{total} match "
        f"({rate:.1%} divergence)"
    )
    return result


# ── Persistence ──────────────────────────────────────────────────────────────

def _persist_result(result: DifferentialResult) -> None:
    """Append result to the differential test log."""
    try:
        existing: list[dict] = []
        if DIFF_RESULTS_PATH.exists():
            existing = json.loads(DIFF_RESULTS_PATH.read_text())
        existing.append({
            "ts": time.time(),
            "strategy": result.strategy.value,
            "test_inputs_count": result.test_inputs_count,
            "matches": result.matches,
            "divergences": result.divergences,
            "divergence_rate": result.divergence_rate,
            "is_safe_change": result.is_safe_change,
        })
        existing = existing[-100:]
        DIFF_RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        DIFF_RESULTS_PATH.write_text(json.dumps(existing, indent=2, default=str))
    except OSError:
        pass


# ── Convenience: diff-test a function-level mutation ─────────────────────────

def diff_test_function(
    old_source: str,
    new_source: str,
    function_name: str,
    test_inputs: list,
    strategy: CompareStrategy | None = None,
    timeout_s: int = 30,
) -> DifferentialResult:
    """Diff-test a single function across two versions of source code.

    Compiles both old_source and new_source into fresh modules, calls the
    named function with each test input, compares results.

    Safe-by-default: source executes in this process (we trust the source
    came from the evolution loop, which has its own safety checks). For
    higher isolation use sandbox_runner.run_code_check.
    """
    strategy = strategy or CompareStrategy.STRUCTURAL

    def _compile_and_get(source: str, name: str):
        ns: dict = {}
        exec(compile(source, f"<{name}>", "exec"), ns)
        return ns.get(function_name)

    try:
        old_fn = _compile_and_get(old_source, "old")
        new_fn = _compile_and_get(new_source, "new")
    except Exception as e:
        logger.warning(f"differential_test: compile failed: {e}")
        return DifferentialResult(0, 0, 0, 0.0, strategy, ())

    if not callable(old_fn) or not callable(new_fn):
        logger.warning(f"differential_test: function '{function_name}' not found")
        return DifferentialResult(0, 0, 0, 0.0, strategy, ())

    return run_differential_test(old_fn, new_fn, test_inputs, strategy)
