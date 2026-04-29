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

CENTRALITY_SPIKE_THRESHOLD = 5      # Existing dependents above this → warn
CAPABILITY_OVERLAP_THRESHOLD = 2    # Existing files with same capability → warn
NEW_FILE_OVERLAP_HARD_THRESHOLD = 3  # New file claiming capability with ≥N owners → HARD reject

# Basenames that legitimately repeat across packages and shouldn't trigger
# the path-duplication hard reject. Anything else is treated as a strong
# parallel-implementation signal.
_DUPLICATION_EXEMPT_BASENAMES: frozenset[str] = frozenset({
    "__init__", "__main__", "test", "tests", "conftest",
    "utils", "helpers", "config", "constants", "types",
    "models", "schema", "schemas",
})


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
    is_new_file: bool = False  # True when the proposing file did not exist before


@dataclass(frozen=True)
class CentralityFinding:
    """A file whose centrality would jump significantly."""
    filepath: str
    current_dependents: int
    projected_dependents: int


@dataclass(frozen=True)
class PathDuplicationFinding:
    """A new file whose basename matches an existing directory or sibling.

    The classic parallel-implementation smell: proposing app/orch/commander.py
    when app/agents/commander/ is already a package, or app/agents/coding.py
    when app/crews/coding_crew.py already exists. Always a HARD reject.
    """
    new_file: str
    existing_path: str
    reason: str


@dataclass(frozen=True)
class ReviewReport:
    """Aggregate output of an architectural review."""
    cycles: tuple[CycleFinding, ...] = ()
    overlaps: tuple[OverlapFinding, ...] = ()
    centrality_spikes: tuple[CentralityFinding, ...] = ()
    path_duplications: tuple[PathDuplicationFinding, ...] = ()

    @property
    def hard_overlaps(self) -> tuple[OverlapFinding, ...]:
        """Overlaps that are severe enough to be hard rejects.

        A NEW file claiming a capability that's already provided by
        ≥NEW_FILE_OVERLAP_HARD_THRESHOLD existing files is treated as
        parallel implementation — almost certainly a duplication smell.
        """
        return tuple(
            o for o in self.overlaps
            if o.is_new_file and len(o.existing_owners) >= NEW_FILE_OVERLAP_HARD_THRESHOLD
        )

    @property
    def has_hard_rejects(self) -> bool:
        """Cycles, path duplications, and severe new-file overlaps are hard rejects.

        - Cycles break Python imports outright
        - Path duplications introduce parallel modules (the audit-driven
          smell we missed in exp_202604290007_1172)
        - New files claiming heavily-shared capabilities almost always
          duplicate rather than extend
        """
        return (
            len(self.cycles) > 0
            or len(self.path_duplications) > 0
            or len(self.hard_overlaps) > 0
        )

    @property
    def has_soft_warnings(self) -> bool:
        """Non-hard overlaps and centrality spikes are advisory only."""
        non_hard_overlaps = [o for o in self.overlaps if o not in self.hard_overlaps]
        return len(non_hard_overlaps) > 0 or len(self.centrality_spikes) > 0

    def summary(self) -> str:
        """Human-readable summary for the critique LLM and logs."""
        if not (self.cycles or self.overlaps or self.centrality_spikes or self.path_duplications):
            return "No architectural concerns"

        parts = []
        if self.cycles:
            parts.append(
                f"{len(self.cycles)} cycle(s) introduced: "
                + "; ".join(" → ".join(c.cycle) for c in self.cycles[:3])
            )
        if self.path_duplications:
            parts.append(
                f"{len(self.path_duplications)} path duplication(s): "
                + "; ".join(
                    f"{d.new_file} duplicates {d.existing_path} ({d.reason})"
                    for d in self.path_duplications[:3]
                )
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
    existing_files: set[str],
) -> list[OverlapFinding]:
    """Detect files that claim capabilities already provided by ≥N others.

    Marks each finding with `is_new_file=True` when the file did not exist
    before this mutation — this distinction matters because a NEW file
    claiming a heavily-shared capability is almost always a parallel
    implementation, while modifying an EXISTING file with new capability
    tags is just normal evolution.

    Uses the same regex-based capability detection as self_model — duplicated
    here so this module stays standalone when self_model is unavailable.
    """
    try:
        from app.self_model import _extract_capabilities
    except ImportError:
        return []

    overlaps: list[OverlapFinding] = []
    for path, source in files_after.items():
        new_caps = _extract_capabilities(source)
        is_new = path not in existing_files
        for cap in new_caps:
            existing = [f for f in capability_map.get(cap, []) if f != path]
            if len(existing) >= CAPABILITY_OVERLAP_THRESHOLD:
                overlaps.append(OverlapFinding(
                    filepath=path,
                    capability=cap,
                    existing_owners=tuple(existing[:5]),
                    is_new_file=is_new,
                ))
    return overlaps


# ── Path duplication detection ──────────────────────────────────────────────

def _detect_path_duplications(
    files_after: dict[str, str],
    existing_paths: set[str],
) -> list[PathDuplicationFinding]:
    """Detect new files whose basename collides with an existing module or directory.

    Catches the parallel-implementation smell exemplified by
    exp_202604290007_1172, which proposed creating ``app/orch/commander.py``
    when ``app/agents/commander/`` already existed as a package.

    Two collision patterns are flagged:

      1. New file basename matches an existing directory in the codebase
         (e.g. ``app/orch/commander.py`` while ``app/agents/commander/``
         is a package directory).
      2. New file basename matches another module's basename in a different
         package (e.g. ``app/agents/coding.py`` while ``app/crews/coding.py``
         already exists).

    Common utility names (``utils``, ``helpers``, ``__init__``, etc.) are
    exempted since they legitimately repeat across packages.
    """
    from pathlib import Path as _Path

    findings: list[PathDuplicationFinding] = []

    # Index existing structure
    existing_directories: set[str] = set()
    existing_basenames: dict[str, str] = {}  # basename → first path
    for ep in existing_paths:
        parts = ep.split("/")
        # Each non-leaf segment is a directory in the codebase
        for segment in parts[:-1]:
            existing_directories.add(segment)
        if ep.endswith(".py"):
            stem = _Path(ep).stem
            existing_basenames.setdefault(stem, ep)

    for path in files_after:
        if not path.endswith(".py"):
            continue
        if path in existing_paths:
            continue  # Existing file being modified, not a new one

        stem = _Path(path).stem
        if stem in _DUPLICATION_EXEMPT_BASENAMES:
            continue

        # Pattern 1: basename matches an existing directory
        if stem in existing_directories:
            findings.append(PathDuplicationFinding(
                new_file=path,
                existing_path=f"{stem}/",
                reason=(
                    f"basename '{stem}' is an existing package directory "
                    f"— refactor that package instead of creating a parallel module"
                ),
            ))
            continue

        # Pattern 2: basename matches another module's basename
        if stem in existing_basenames:
            findings.append(PathDuplicationFinding(
                new_file=path,
                existing_path=existing_basenames[stem],
                reason=(
                    f"basename '{stem}' already used by {existing_basenames[stem]} "
                    f"— refactor or rename to avoid parallel implementations"
                ),
            ))

    return findings


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

    existing_files: set[str] = set(model.modules.keys())

    cycles = _detect_cycles(py_files, forward_graph)
    overlaps = _detect_overlaps(py_files, model.capability_map, existing_files)
    spikes = _detect_centrality_spikes(py_files, model.dependency_graph)
    path_dupes = _detect_path_duplications(py_files, existing_files)

    report = ReviewReport(
        cycles=tuple(cycles),
        overlaps=tuple(overlaps),
        centrality_spikes=tuple(spikes),
        path_duplications=tuple(path_dupes),
    )

    if report.has_hard_rejects or report.has_soft_warnings:
        logger.info(f"architectural_review: {report.summary()}")

    return report
