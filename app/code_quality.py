"""
code_quality.py — Mechanical code quality measurement for evolution gating.

The audit found that mutations could pass all gates technically while still
producing inelegant code: nested try/except scaffolding, dropped type hints,
duplicated logic, magic numbers. This module measures four mechanical quality
dimensions per file:

  1. Type-hint coverage    — fraction of public functions with full annotations
  2. Docstring coverage    — fraction of public functions with docstrings
  3. Cyclomatic complexity — average per function (lower is better)
  4. Lint score            — fraction of ruff checks that pass (if available)

Quality is computed *per file* before and after a mutation. The aggregate
delta becomes a gate in `experiment_runner`: if any single touched file's
quality drops by more than `QUALITY_REGRESSION_THRESHOLD`, the mutation
is forced to discard regardless of its functional delta.

The module is fail-soft: if any tool is unavailable (no ruff, no radon),
that dimension is skipped and the remaining ones contribute. A completely
unavailable backend returns a neutral score (1.0) rather than failing.

Reference: complements the existing AST-based safety checks in
auto_deployer.py with a quality dimension that previously had no signal.
"""

from __future__ import annotations

import ast
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


# ── Configuration ────────────────────────────────────────────────────────────

QUALITY_REGRESSION_THRESHOLD = 0.10  # Per-file drop > 10% → reject mutation
COMPLEXITY_TARGET = 10.0             # McCabe complexity above this is a smell
COMPLEXITY_HARD_LIMIT = 25.0         # Above this, score floors at 0


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class QualityScore:
    """One file's quality measurement."""
    type_coverage: float        # 0.0–1.0
    docstring_coverage: float   # 0.0–1.0
    complexity_score: float     # 0.0–1.0 (1.0 = simple, 0.0 = hopelessly complex)
    lint_score: float           # 0.0–1.0 (1.0 = no lint issues)
    composite: float            # weighted average

    def to_dict(self) -> dict:
        return {
            "type_coverage": self.type_coverage,
            "docstring_coverage": self.docstring_coverage,
            "complexity_score": self.complexity_score,
            "lint_score": self.lint_score,
            "composite": self.composite,
        }


@dataclass(frozen=True)
class QualityDelta:
    """Quality change for a single file."""
    filepath: str
    before: QualityScore
    after: QualityScore
    delta: float           # composite_after - composite_before
    is_regression: bool    # delta < -QUALITY_REGRESSION_THRESHOLD


@dataclass(frozen=True)
class MutationQualityReport:
    """Aggregate report for an entire mutation (potentially multi-file)."""
    file_deltas: tuple[QualityDelta, ...]
    worst_regression: float
    has_regression: bool
    summary: str


# ── AST-based scoring (always available) ────────────────────────────────────

def _is_public_function(node: ast.AST) -> bool:
    """A FunctionDef/AsyncFunctionDef is public if its name doesn't start with _."""
    return (
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    )


def _is_fully_typed(node: ast.AST) -> bool:
    """Function has type annotations on all positional args + return type.

    Skips `self` and `cls`. Functions with no parameters require only a
    return annotation. Star-args (`*args`, `**kwargs`) are allowed without
    annotations (they're idiomatic without).
    """
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False

    args = node.args.args
    # Skip self/cls (first arg of method)
    if args and args[0].arg in ("self", "cls"):
        args = args[1:]

    for arg in args:
        if arg.annotation is None:
            return False

    # Return type required (None is acceptable as annotation)
    return node.returns is not None


def _has_docstring(node: ast.AST) -> bool:
    """Check if a function/class has a docstring."""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return False
    return ast.get_docstring(node) is not None


def _measure_ast_dimensions(source: str) -> tuple[float, float]:
    """Compute type-hint and docstring coverage from source via AST.

    Returns (type_coverage, docstring_coverage), each 0.0–1.0.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0.0, 0.0

    public_fns = [n for n in ast.walk(tree) if _is_public_function(n)]
    if not public_fns:
        return 1.0, 1.0  # Vacuously satisfied — no public functions to score

    typed = sum(1 for fn in public_fns if _is_fully_typed(fn))
    documented = sum(1 for fn in public_fns if _has_docstring(fn))

    return typed / len(public_fns), documented / len(public_fns)


# ── Cyclomatic complexity (radon if available) ──────────────────────────────

def _measure_complexity(source: str) -> float:
    """Average McCabe complexity, normalized to 0.0–1.0 (higher = simpler).

    Uses `radon` if installed; falls back to a basic AST count of branching
    statements when radon is unavailable.
    """
    average_complexity = _radon_average(source)
    if average_complexity is None:
        average_complexity = _basic_complexity(source)

    if average_complexity is None:
        return 1.0  # No measurable functions → neutral

    if average_complexity >= COMPLEXITY_HARD_LIMIT:
        return 0.0
    if average_complexity <= COMPLEXITY_TARGET:
        return 1.0
    # Linear interpolation between target and hard limit
    span = COMPLEXITY_HARD_LIMIT - COMPLEXITY_TARGET
    return max(0.0, 1.0 - (average_complexity - COMPLEXITY_TARGET) / span)


def _radon_average(source: str) -> float | None:
    """Use radon to get average cyclomatic complexity, or None if unavailable."""
    try:
        from radon.complexity import cc_visit
    except ImportError:
        return None
    try:
        blocks = cc_visit(source)
        if not blocks:
            return None
        return sum(b.complexity for b in blocks) / len(blocks)
    except Exception:
        return None


def _basic_complexity(source: str) -> float | None:
    """Fallback complexity: count branching nodes per function via AST."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if not functions:
        return None

    branching_types = (ast.If, ast.For, ast.While, ast.Try, ast.ExceptHandler, ast.With, ast.BoolOp)
    complexities = []
    for fn in functions:
        score = 1  # baseline
        for node in ast.walk(fn):
            if isinstance(node, branching_types):
                score += 1
        complexities.append(score)

    return sum(complexities) / len(complexities) if complexities else None


# ── Lint scoring (ruff if available) ────────────────────────────────────────

def _measure_lint(source: str) -> float:
    """Run ruff against the source, return pass rate (issues per kloc).

    Returns 1.0 if ruff is unavailable or there are zero issues.
    Returns score in [0.0, 1.0] proportional to issues per 1000 lines.
    """
    issues = _ruff_issue_count(source)
    if issues is None:
        return 1.0  # Tool unavailable → neutral

    line_count = max(1, source.count("\n"))
    issues_per_kloc = (issues * 1000) / line_count

    # ≤ 5 issues/kloc → score 1.0
    # ≥ 50 issues/kloc → score 0.0
    if issues_per_kloc <= 5:
        return 1.0
    if issues_per_kloc >= 50:
        return 0.0
    return max(0.0, 1.0 - (issues_per_kloc - 5) / 45)


def _ruff_issue_count(source: str) -> int | None:
    """Run ruff on the given source, return issue count (None if unavailable)."""
    try:
        # Pipe source via stdin, parse the line-prefixed default output
        result = subprocess.run(
            ["ruff", "check", "--quiet", "--no-cache", "--output-format=concise", "-"],
            input=source,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    except Exception as exc:
        logger.debug(f"code_quality: ruff invocation failed: {exc}")
        return None

    # ruff prints one issue per line in concise format
    if not result.stdout:
        return 0
    return sum(1 for line in result.stdout.splitlines() if line.strip())


# ── Composite scoring ──────────────────────────────────────────────────────

# Weights sum to 1.0 — type hints and docstrings carry the most signal
# because they're the most LLM-droppable; complexity and lint catch
# subtler regressions.
_WEIGHTS = {
    "type_coverage": 0.35,
    "docstring_coverage": 0.20,
    "complexity_score": 0.25,
    "lint_score": 0.20,
}


def measure_file_quality(source: str) -> QualityScore:
    """Compute the four quality dimensions for one file's source code."""
    type_cov, doc_cov = _measure_ast_dimensions(source)
    complexity = _measure_complexity(source)
    lint = _measure_lint(source)

    composite = (
        type_cov * _WEIGHTS["type_coverage"]
        + doc_cov * _WEIGHTS["docstring_coverage"]
        + complexity * _WEIGHTS["complexity_score"]
        + lint * _WEIGHTS["lint_score"]
    )

    return QualityScore(
        type_coverage=round(type_cov, 3),
        docstring_coverage=round(doc_cov, 3),
        complexity_score=round(complexity, 3),
        lint_score=round(lint, 3),
        composite=round(composite, 3),
    )


def measure_file_at_path(path: Path) -> QualityScore | None:
    """Convenience: read a file and compute its quality. Returns None if
    the file can't be read."""
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return measure_file_quality(source)


# ── Mutation-level gate ────────────────────────────────────────────────────

def evaluate_mutation_quality(
    files_before: dict[str, str | None],
    files_after: dict[str, str],
) -> MutationQualityReport:
    """Compare quality of each touched file before vs after a mutation.

    Args:
        files_before: {path: source_or_None_if_new_file}
        files_after:  {path: source}

    Returns:
        MutationQualityReport with per-file deltas and an aggregate verdict.
    """
    deltas: list[QualityDelta] = []

    # Score only Python source files — markdown/json files have no quality dimension
    py_files = {p: src for p, src in files_after.items() if p.endswith(".py")}

    for path, after_src in py_files.items():
        before_src = files_before.get(path)

        # New file: score against an empty baseline (0.0 composite)
        if before_src is None:
            after_score = measure_file_quality(after_src)
            empty_baseline = QualityScore(0.0, 0.0, 0.0, 0.0, 0.0)
            deltas.append(QualityDelta(
                filepath=path,
                before=empty_baseline,
                after=after_score,
                delta=after_score.composite,  # New files always show positive delta
                is_regression=False,
            ))
            continue

        before_score = measure_file_quality(before_src)
        after_score = measure_file_quality(after_src)
        delta = after_score.composite - before_score.composite

        deltas.append(QualityDelta(
            filepath=path,
            before=before_score,
            after=after_score,
            delta=round(delta, 3),
            is_regression=delta < -QUALITY_REGRESSION_THRESHOLD,
        ))

    if not deltas:
        return MutationQualityReport(
            file_deltas=(),
            worst_regression=0.0,
            has_regression=False,
            summary="No Python files in mutation",
        )

    worst = min(d.delta for d in deltas)
    has_regression = any(d.is_regression for d in deltas)

    if has_regression:
        regressed = [d for d in deltas if d.is_regression]
        summary = (
            f"Quality regression in {len(regressed)} file(s): "
            + "; ".join(
                f"{d.filepath} ({d.delta:+.2f}: type={d.before.type_coverage:.2f}→{d.after.type_coverage:.2f}, "
                f"docs={d.before.docstring_coverage:.2f}→{d.after.docstring_coverage:.2f})"
                for d in regressed[:3]
            )
        )
    else:
        summary = f"No quality regression (worst delta {worst:+.2f})"

    return MutationQualityReport(
        file_deltas=tuple(deltas),
        worst_regression=round(worst, 3),
        has_regression=has_regression,
        summary=summary,
    )


# ── Diagnostic helpers ──────────────────────────────────────────────────────

def get_codebase_quality_snapshot(root: Path = Path("/app/app")) -> dict:
    """Aggregate quality metrics across the whole codebase.

    Designed for the dashboard. Returns counts and average scores per
    dimension so style drift is observable over time.
    """
    if not root.exists():
        return {"file_count": 0}

    scores: list[QualityScore] = []
    for py_file in root.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        score = measure_file_at_path(py_file)
        if score is not None:
            scores.append(score)

    if not scores:
        return {"file_count": 0}

    def avg(field: str) -> float:
        return round(sum(getattr(s, field) for s in scores) / len(scores), 3)

    return {
        "file_count": len(scores),
        "avg_type_coverage": avg("type_coverage"),
        "avg_docstring_coverage": avg("docstring_coverage"),
        "avg_complexity_score": avg("complexity_score"),
        "avg_lint_score": avg("lint_score"),
        "avg_composite": avg("composite"),
        "fully_typed_files": sum(1 for s in scores if s.type_coverage >= 0.99),
        "fully_documented_files": sum(1 for s in scores if s.docstring_coverage >= 0.99),
    }
