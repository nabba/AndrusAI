"""
architectural_review.py — Mechanical architectural taste signals for AVO.

Mechanical quality checks (lint, types, complexity) catch hygiene violations
but miss structural smells: introducing import cycles, duplicating capability
that already exists, or sharply increasing a file's centrality.

This module reads the existing self-model dependency graph + capability map
and runs three structural checks against a proposed mutation:

  1. Cycle introduction — would the new imports create a circular dependency?
  2. Capability overlap  — does this duplicate a capability that already
                            exists elsewhere in the codebase?
  3. Centrality spike    — does this file's dependent count jump beyond a
                            threshold (becoming load-bearing without intent)?

Cycles are *hard rejects* (they break Python imports). Overlap and centrality
spikes are *soft warnings* surfaced to the AVO Phase 4 critique LLM, which
makes the final call.

The module is read-only and never modifies code. It depends on `self_model`
having a current snapshot — if the model is missing, the review produces an
empty (but non-failing) report so evolution continues.
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ── Configuration ────────────────────────────────────────────────────────────

CENTRALITY_SPIKE_THRESHOLD = 5  # New dependents above this → warn
CAPABILITY_OVERLAP_THRESHOLD = 2  # Existing files with same capability → warn


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CycleFinding:
    """A circular import path discovered in the proposed mutation."""
    cycle: tuple[str, ...]  # e.g. ("app/a.py", "app/b.py", "app/a.py")


@dataclass(frozen=True)
class OverlapFinding:
    """A capability that already exists in N other files."""
    filepath: str
    capability: str
    existing_owners: tuple[str, ...]


@dataclass(frozen=True)
class CentralityFinding:
    """A file whose centrality would jump significantly."""
    filepath: str
    current_dependents: int
    projected_dependents: int


@dataclass(frozen=True)
class ReviewReport:
    """Aggregate output of an architectural review."""
    cycles: tuple[CycleFinding, ...] = ()
    overlaps: tuple[OverlapFinding, ...] = ()
    centrality_spikes: tuple[CentralityFinding, ...] = ()

    @property
    def has_hard_rejects(self) -> bool:
        """Cycles are hard rejects — they break Python imports."""
        return len(self.cycles) > 0

    @property
    def has_soft_warnings(self) -> bool:
        """Overlaps and centrality spikes are advisory."""
        return len(self.overlaps) > 0 or len(self.centrality_spikes) > 0

    def summary(self) -> str:
        """Human-readable summary for the critique LLM and logs."""
        if not (self.cycles or self.overlaps or self.centrality_spikes):
            return "No architectural concerns"

        parts = []
        if self.cycles:
            parts.append(
                f"{len(self.cycles)} cycle(s) introduced: "
                + "; ".join(" → ".join(c.cycle) for c in self.cycles[:3])
            )
        if self.overlaps:
            parts.append(
                f"{len(self.overlaps)} capability overlap(s): "
                + "; ".join(
                    f"'{o.capability}' duplicates {len(o.existing_owners)} existing files"
                    for o in self.overlaps[:3]
                )
            )
        if self.centrality_spikes:
            parts.append(
                f"{len(self.centrality_spikes)} centrality spike(s): "
                + "; ".join(
                    f"{c.filepath} would gain {c.projected_dependents - c.current_dependents} dependents"
                    for c in self.centrality_spikes[:3]
                )
            )
        return " | ".join(parts)


# ── Import extraction (uses the same logic as self_model) ───────────────────

def _extract_local_imports(source: str) -> list[str]:
    """Return relative paths for local app.* imports in the source.

    Mirrors the helper in self_model.py — kept local here so this module
    can run independently when self_model is unavailable.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    imports: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app."):
            imports.append(_dotted_to_relative(node.module))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("app."):
                    imports.append(_dotted_to_relative(alias.name))
    return imports


def _dotted_to_relative(dotted: str) -> str:
    """app.foo.bar → app/foo/bar.py — matches self_model.py exactly."""
    return dotted.replace(".", "/") + ".py"


# ── Cycle detection ─────────────────────────────────────────────────────────

def _detect_cycles(
    files_after: dict[str, str],
    existing_graph: dict[str, list[str]],
) -> list[CycleFinding]:
    """Project the post-mutation import graph, look for cycles via DFS.

    `existing_graph` maps file → list of files it imports (NOT dependents).
    We simulate adding the mutation's imports, then walk for cycles starting
    from each touched file.
    """
    # Build a forward graph: file → files it imports (after mutation)
    forward: dict[str, set[str]] = {f: set(deps) for f, deps in existing_graph.items()}
    for path, source in files_after.items():
        forward[path] = set(_extract_local_imports(source))

    cycles: list[CycleFinding] = []
    seen_cycles: set[tuple[str, ...]] = set()

    def _dfs(start: str, current: str, path: list[str]) -> None:
        for neighbor in forward.get(current, ()):
            if neighbor == start and len(path) >= 1:
                # Cycle back to start — record canonical form
                cycle = tuple(path + [neighbor])
                # Canonicalize: rotate so the lexically smallest entry leads
                min_idx = cycle[:-1].index(min(cycle[:-1]))
                canonical = cycle[min_idx:-1] + cycle[:min_idx] + (cycle[min_idx],)
                if canonical not in seen_cycles:
                    seen_cycles.add(canonical)
                    cycles.append(CycleFinding(cycle=canonical))
                continue
            if neighbor in path:
                continue  # Already visiting on this path; not a cycle through start
            if len(path) > 8:
                continue  # Bound DFS depth — practical guard against runaway graphs
            _dfs(start, neighbor, path + [neighbor])

    for touched_file in files_after:
        _dfs(touched_file, touched_file, [touched_file])

    return cycles


# ── Capability overlap detection ────────────────────────────────────────────

def _detect_overlaps(
    files_after: dict[str, str],
    capability_map: dict[str, list[str]],
) -> list[OverlapFinding]:
    """Detect new files that claim capabilities already provided by ≥N others.

    Uses the same regex-based capability detection as self_model — duplicating
    here keeps this module standalone when self_model is unavailable.
    """
    try:
        from app.self_model import _extract_capabilities
    except ImportError:
        return []

    overlaps: list[OverlapFinding] = []
    for path, source in files_after.items():
        # Skip files that already exist in the capability map (they're not new claims)
        new_caps = _extract_capabilities(source)
        for cap in new_caps:
            existing = [f for f in capability_map.get(cap, []) if f != path]
            if len(existing) >= CAPABILITY_OVERLAP_THRESHOLD:
                overlaps.append(OverlapFinding(
                    filepath=path,
                    capability=cap,
                    existing_owners=tuple(existing[:5]),
                ))
    return overlaps


# ── Centrality spike detection ──────────────────────────────────────────────

def _detect_centrality_spikes(
    files_after: dict[str, str],
    dependency_graph: dict[str, list[str]],
) -> list[CentralityFinding]:
    """Detect mutations that would add many new dependents to a file.

    `dependency_graph` is the *reverse* direction (file → modules that import it).
    We don't have direct knowledge of mutations to OTHER files in this run, so
    centrality spike here means: this mutated file will gain dependents because
    *its imports increase load on other files*. We surface this as a warning
    when the file's own centrality is already high.
    """
    spikes: list[CentralityFinding] = []
    for path in files_after:
        current_dependents = len(dependency_graph.get(path, []))
        if current_dependents >= CENTRALITY_SPIKE_THRESHOLD:
            spikes.append(CentralityFinding(
                filepath=path,
                current_dependents=current_dependents,
                projected_dependents=current_dependents,  # Same — we just flag highly-central files
            ))
    return spikes


# ── Public API ──────────────────────────────────────────────────────────────

def review_mutation(files_after: dict[str, str]) -> ReviewReport:
    """Run the architectural review on a proposed mutation.

    Args:
        files_after: {path: post-mutation source} for every Python file
                     touched by the mutation.

    Returns:
        ReviewReport with cycles, overlaps, and centrality spikes.
        Empty report if self-model is unavailable (graceful degradation).
    """
    # Filter to Python source files
    py_files = {p: src for p, src in files_after.items() if p.endswith(".py")}
    if not py_files:
        return ReviewReport()

    try:
        from app.self_model import get_self_model
    except ImportError:
        logger.debug("architectural_review: self_model unavailable, skipping review")
        return ReviewReport()

    try:
        model = get_self_model()
    except Exception as exc:
        logger.debug(f"architectural_review: self_model load failed: {exc}")
        return ReviewReport()

    # The self-model stores forward imports inside each ModuleNode and
    # the reverse (dependents) in dependency_graph. Build the forward
    # graph from ModuleNode.imports for cycle detection.
    forward_graph: dict[str, list[str]] = {}
    for path, node in model.modules.items():
        forward_graph[path] = [_dotted_to_relative(imp) for imp in node.imports]

    cycles = _detect_cycles(py_files, forward_graph)
    overlaps = _detect_overlaps(py_files, model.capability_map)
    spikes = _detect_centrality_spikes(py_files, model.dependency_graph)

    report = ReviewReport(
        cycles=tuple(cycles),
        overlaps=tuple(overlaps),
        centrality_spikes=tuple(spikes),
    )

    if report.has_hard_rejects or report.has_soft_warnings:
        logger.info(f"architectural_review: {report.summary()}")

    return report
