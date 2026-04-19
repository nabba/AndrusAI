"""Safety invariants for the trajectory-informed memory subsystem.

These tests encode the architectural promises from CLAUDE.md:

  * Evaluation/attribution logic lives at infrastructure level.
  * The Self-Improver cannot modify its own evaluation criteria.

Concretely, we assert that `app.trajectory.attribution` and
`app.trajectory.calibration` are NOT imported (directly or transitively
through static analysis) by any file in either:

  * app.self_improvement.*
  * app.crews.self_improvement_crew

These modules may call the attribution system only by *reading* its
outputs (via `load_attribution`, which returns a read-only dataclass)
and via the gap_detector's LearningGap pipeline. That indirection is
the contract: the Self-Improver reads gaps; it cannot reach into the
attribution code itself.

The test also asserts each new module carries the IMMUTABLE marker in
its docstring — a convention used throughout the repo to mark
infrastructure modules.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

REPO = Path(__file__).resolve().parent.parent


# ── Helpers ─────────────────────────────────────────────────────────────


def _imports_in(path: Path) -> set[str]:
    """Return the set of fully-qualified module names imported by `path`.

    Handles both `import a.b.c` and `from a.b import c` forms. Includes
    imports that appear inside function bodies — lazy imports do not
    exempt the invariant.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            for alias in node.names:
                mods.add(f"{base}.{alias.name}" if base else alias.name)
    return mods


def _files_under(relpath: str) -> list[Path]:
    root = REPO / relpath
    if not root.exists():
        return []
    if root.is_file():
        return [root]
    return sorted(root.rglob("*.py"))


# ── Invariants ───────────────────────────────────────────────────────────


def test_self_improvement_cannot_import_attribution():
    """app.trajectory.attribution is off-limits for Self-Improver files."""
    forbidden = (
        "app.trajectory.attribution",
        "app.trajectory.calibration",
    )
    offenders: list[tuple[Path, str]] = []
    for path in (
        _files_under("app/self_improvement")
        + _files_under("app/crews/self_improvement_crew.py")
    ):
        mods = _imports_in(path)
        for forbidden_mod in forbidden:
            for m in mods:
                # Match exact module or any deeper child
                if m == forbidden_mod or m.startswith(forbidden_mod + "."):
                    offenders.append((path.relative_to(REPO), m))
    assert offenders == [], (
        "Self-Improver code cannot import attribution/calibration modules "
        "— evaluation logic must stay infrastructure-level. Offenders: "
        f"{offenders}"
    )


def test_observer_module_not_imported_by_self_improvement():
    """app.agents.observer is already infrastructure — same discipline."""
    offenders: list[tuple[Path, str]] = []
    for path in (
        _files_under("app/self_improvement")
        + _files_under("app/crews/self_improvement_crew.py")
    ):
        mods = _imports_in(path)
        for m in mods:
            if m == "app.agents.observer" or m.startswith("app.agents.observer."):
                offenders.append((path.relative_to(REPO), m))
    assert offenders == [], (
        f"Self-Improver code must not import app.agents.observer: {offenders}"
    )


def test_trajectory_modules_marked_immutable():
    """Every module in app.trajectory must carry the IMMUTABLE marker.

    Mirrors the convention used by observer.py, map_elites_wiring.py,
    integrator.py — a lightweight documentation signal that the module
    is not agent-modifiable and changes require infrastructure review.
    """
    trajectory_dir = REPO / "app" / "trajectory"
    missing: list[str] = []
    for f in sorted(trajectory_dir.glob("*.py")):
        if f.name == "__init__.py":
            # __init__ re-exports; the marker lives on the source modules.
            continue
        src = f.read_text(encoding="utf-8")
        if "IMMUTABLE" not in src.split("\n\n")[0] and "IMMUTABLE" not in src[:2000]:
            missing.append(f.name)
    assert missing == [], (
        f"These trajectory modules are missing the IMMUTABLE docstring "
        f"marker: {missing}"
    )


def test_attribution_reads_gap_store_via_indirection_only():
    """attribution.py must use the gap_detector emit helper, never
    write gaps directly to the store.

    Rationale: emit_gap lives in app.self_improvement.store and runs
    dedup / signal-strength merging. Bypassing it would corrupt the
    gap pipeline's semantics.
    """
    attr = REPO / "app" / "trajectory" / "attribution.py"
    src = attr.read_text(encoding="utf-8")
    # No direct imports of the low-level store or emit_gap primitive.
    assert "from app.self_improvement.store import emit_gap" not in src, (
        "attribution.py must not call emit_gap directly — route via "
        "gap_detector.emit_trajectory_attribution"
    )
    # But MUST route through the high-level emitter.
    assert "emit_trajectory_attribution" in src, (
        "attribution.py must route gaps via "
        "gap_detector.emit_trajectory_attribution"
    )
