"""
self_model.py — Static dependency graph, capability map, and hot/cold path classification.

The system needs to understand its own structure before it can mutate itself
intelligently. This module builds a periodically-refreshed self-model by
parsing the codebase: which modules import which, which capabilities each
module provides, and which files lie on the hot path (executed during user
requests) versus the cold path (background work only).

The AVO planner consumes this model when proposing mutations:
  - Dependency graph: warns when a mutation has many downstream dependents
  - Capability map: identifies which file owns a needed capability
  - Hot/cold classification: applies extra scrutiny to hot-path mutations

Refreshed daily by the idle_scheduler. Cached in workspace/self_model.json.

This is read-only static analysis — it never modifies code. Pure observation.
"""

from __future__ import annotations

import ast
import json
import logging
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

APP_ROOT = Path("/app/app")
WORKSPACE_ROOT = Path("/app/workspace")
SELF_MODEL_PATH = WORKSPACE_ROOT / "self_model.json"
HOT_PATH_DEPTH = 2  # imports of imports of main.py = depth 2
MODEL_TTL_HOURS = 24


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ModuleNode:
    """One module in the codebase."""
    path: str                          # relative to /app/, e.g. "app/agents/researcher.py"
    imports: tuple[str, ...]           # local imports (other app/ modules)
    exports: tuple[str, ...]           # public top-level names (no underscore prefix)
    line_count: int
    is_hot_path: bool                  # reachable from app/main.py within HOT_PATH_DEPTH
    capability_tags: tuple[str, ...]   # extracted from docstring


@dataclass
class SelfModel:
    """The system's understanding of itself."""
    modules: dict[str, ModuleNode] = field(default_factory=dict)
    capability_map: dict[str, list[str]] = field(default_factory=dict)
    dependency_graph: dict[str, list[str]] = field(default_factory=dict)  # file → dependents
    built_at: float = 0.0
    build_duration_s: float = 0.0

    def to_dict(self) -> dict:
        return {
            "modules": {p: asdict(m) for p, m in self.modules.items()},
            "capability_map": self.capability_map,
            "dependency_graph": self.dependency_graph,
            "built_at": self.built_at,
            "build_duration_s": self.build_duration_s,
            "module_count": len(self.modules),
            "hot_path_count": sum(1 for m in self.modules.values() if m.is_hot_path),
        }

    @classmethod
    def from_dict(cls, d: dict) -> SelfModel:
        modules = {
            p: ModuleNode(
                path=m["path"],
                imports=tuple(m["imports"]),
                exports=tuple(m["exports"]),
                line_count=m["line_count"],
                is_hot_path=m["is_hot_path"],
                capability_tags=tuple(m["capability_tags"]),
            )
            for p, m in d.get("modules", {}).items()
        }
        return cls(
            modules=modules,
            capability_map=d.get("capability_map", {}),
            dependency_graph=d.get("dependency_graph", {}),
            built_at=d.get("built_at", 0.0),
            build_duration_s=d.get("build_duration_s", 0.0),
        )


# ── Capability tag extraction (heuristic, no LLM) ────────────────────────────

# Maps regex patterns in module docstrings/code to capability tags.
# Designed to be expandable: new categories require only adding a pattern.
_CAPABILITY_PATTERNS: dict[str, re.Pattern] = {
    "evolution": re.compile(r"\b(evolution|mutate|variant|fitness|MAP-?Elites|island)\b", re.I),
    "evaluation": re.compile(r"\b(evaluat|score|metric|benchmark|judge|composite)\b", re.I),
    "memory": re.compile(r"\b(memory|chromadb|recall|embed|vector store|mem0)\b", re.I),
    "agent": re.compile(r"\b(agent|crew|specialist|delegat)\b", re.I),
    "tool": re.compile(r"\b(tool|web search|file manager|api call)\b", re.I),
    "safety": re.compile(r"\b(safety|sanitize|guardian|circuit breaker|vetting)\b", re.I),
    "deploy": re.compile(r"\b(deploy|rollback|canary|hot.?reload|workspace.?commit)\b", re.I),
    "error_handling": re.compile(r"\b(error|exception|self.?heal|recovery|MAST|failure)\b", re.I),
    "llm": re.compile(r"\b(LLM|Anthropic|OpenAI|Claude|GPT|Gemini|Sonnet|Opus|completion)\b", re.I),
    "subia": re.compile(r"\b(SUBIA|subjectivity|homeostasis|kernel|consciousness)\b", re.I),
    "monitoring": re.compile(r"\b(monitor|metric|health|telemetry|observability|dashboard)\b", re.I),
    "knowledge": re.compile(r"\b(skill|knowledge base|RAG|retriev|wiki|philosophy)\b", re.I),
}


def _extract_capabilities(source: str) -> tuple[str, ...]:
    """Extract capability tags from a module's docstring and code.

    Pure heuristic — no LLM. Scans first 2000 chars (docstring + early code).
    """
    head = source[:2000]
    matches = []
    for tag, pattern in _CAPABILITY_PATTERNS.items():
        if pattern.search(head):
            matches.append(tag)
    return tuple(matches)


# ── AST parsing ──────────────────────────────────────────────────────────────

def _parse_imports_and_exports(source: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Extract local app.* imports and public top-level names from source code."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return (), ()

    imports: list[str] = []
    exports: list[str] = []

    for node in tree.body:
        # Local imports: `from app.X import Y` or `import app.X`
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("app."):
                imports.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("app."):
                    imports.append(alias.name)

        # Public top-level definitions
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                exports.append(node.name)

    return tuple(sorted(set(imports))), tuple(sorted(set(exports)))


def _module_path_to_dotted(path: Path, root: Path) -> str:
    """Convert /app/app/foo/bar.py → app.foo.bar"""
    rel = path.relative_to(root.parent)  # path relative to /app/
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _dotted_to_relative(dotted: str) -> str:
    """Convert app.foo.bar → app/foo/bar.py (best-effort)."""
    return dotted.replace(".", "/") + ".py"


# ── Hot path classification ──────────────────────────────────────────────────

def _classify_hot_paths(
    modules: dict[str, ModuleNode],
    seed: str = "app/main.py",
    max_depth: int = HOT_PATH_DEPTH,
) -> set[str]:
    """BFS from main.py to identify modules on the hot path.

    Hot = imported by main.py within max_depth hops. These are modules that
    execute during user requests. Cold = only reached through background jobs
    or test harnesses.
    """
    if seed not in modules:
        # No main module → can't classify; treat all as hot to be safe
        return set(modules.keys())

    hot: set[str] = {seed}
    frontier: list[tuple[str, int]] = [(seed, 0)]

    while frontier:
        current, depth = frontier.pop(0)
        if depth >= max_depth:
            continue

        node = modules.get(current)
        if not node:
            continue

        for imp in node.imports:
            # Resolve dotted import to relative path
            candidate = _dotted_to_relative(imp)
            if candidate in modules and candidate not in hot:
                hot.add(candidate)
                frontier.append((candidate, depth + 1))

    return hot


# ── Build pipeline ───────────────────────────────────────────────────────────

def build_self_model(root: Path = APP_ROOT) -> SelfModel:
    """Walk the codebase and produce a fresh SelfModel.

    Args:
        root: Root directory to scan (defaults to /app/app).

    Returns:
        SelfModel with modules, dependencies, capabilities, hot-path classification.
    """
    start = time.monotonic()
    raw_modules: dict[str, ModuleNode] = {}

    if not root.exists():
        logger.warning(f"self_model: root {root} does not exist")
        return SelfModel(built_at=time.time(), build_duration_s=0.0)

    for py_file in root.rglob("*.py"):
        # Skip __pycache__ and migrations
        if "__pycache__" in py_file.parts:
            continue
        try:
            source = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        rel_path = str(py_file.relative_to(root.parent))  # "app/foo/bar.py"
        imports, exports = _parse_imports_and_exports(source)
        capabilities = _extract_capabilities(source)
        line_count = source.count("\n") + 1

        raw_modules[rel_path] = ModuleNode(
            path=rel_path,
            imports=imports,
            exports=exports,
            line_count=line_count,
            is_hot_path=False,  # set in second pass
            capability_tags=capabilities,
        )

    # Hot path classification (BFS from main.py)
    hot_paths = _classify_hot_paths(raw_modules)

    # Rebuild with hot_path flag set
    modules = {
        path: ModuleNode(
            path=node.path,
            imports=node.imports,
            exports=node.exports,
            line_count=node.line_count,
            is_hot_path=path in hot_paths,
            capability_tags=node.capability_tags,
        )
        for path, node in raw_modules.items()
    }

    # Capability map: capability → list of files providing it
    capability_map: dict[str, list[str]] = {}
    for path, node in modules.items():
        for tag in node.capability_tags:
            capability_map.setdefault(tag, []).append(path)

    # Dependency graph: file → list of dependents (modules that import it)
    dependency_graph: dict[str, list[str]] = {}
    for path, node in modules.items():
        for imp in node.imports:
            target = _dotted_to_relative(imp)
            if target in modules:
                dependency_graph.setdefault(target, []).append(path)

    duration = time.monotonic() - start
    logger.info(
        f"self_model: built in {duration:.2f}s — "
        f"{len(modules)} modules, {len(hot_paths)} on hot path, "
        f"{len(capability_map)} capability tags"
    )

    return SelfModel(
        modules=modules,
        capability_map=capability_map,
        dependency_graph=dependency_graph,
        built_at=time.time(),
        build_duration_s=duration,
    )


# ── Persistence ──────────────────────────────────────────────────────────────

def save_self_model(model: SelfModel, path: Path = SELF_MODEL_PATH) -> bool:
    """Persist the self-model to JSON. Returns True on success."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(model.to_dict(), indent=2, default=str))
        return True
    except OSError as e:
        logger.warning(f"self_model: save failed: {e}")
        return False


def load_self_model(path: Path = SELF_MODEL_PATH) -> SelfModel | None:
    """Load self-model from disk. Returns None if not found or invalid."""
    if not path.exists():
        return None
    try:
        return SelfModel.from_dict(json.loads(path.read_text()))
    except (json.JSONDecodeError, OSError, KeyError) as e:
        logger.warning(f"self_model: load failed: {e}")
        return None


# ── Cached accessor with TTL ─────────────────────────────────────────────────

_cached_model: SelfModel | None = None


def get_self_model(force_rebuild: bool = False) -> SelfModel:
    """Get the cached self-model, rebuilding if stale (>24h) or missing.

    The first call after restart loads from disk if available. If the disk
    copy is stale or missing, rebuilds from source.
    """
    global _cached_model

    if _cached_model is None and not force_rebuild:
        _cached_model = load_self_model()

    age_hours = (time.time() - _cached_model.built_at) / 3600 if _cached_model else float("inf")

    if force_rebuild or _cached_model is None or age_hours > MODEL_TTL_HOURS:
        _cached_model = build_self_model()
        save_self_model(_cached_model)

    return _cached_model


# ── Query API ────────────────────────────────────────────────────────────────

def get_dependents(filepath: str) -> list[str]:
    """Return files that import the given file (direct dependents only)."""
    model = get_self_model()
    return list(model.dependency_graph.get(filepath, []))


def get_capability_owners(capability: str) -> list[str]:
    """Return files providing the given capability tag."""
    model = get_self_model()
    return list(model.capability_map.get(capability, []))


def is_hot_path(filepath: str) -> bool:
    """Check if a file is on the hot path (executes during user requests)."""
    model = get_self_model()
    node = model.modules.get(filepath)
    return node.is_hot_path if node else False


def get_module_info(filepath: str) -> ModuleNode | None:
    """Get the ModuleNode for a specific file."""
    model = get_self_model()
    return model.modules.get(filepath)


def get_centrality_score(filepath: str) -> float:
    """Compute a centrality score: |dependents| normalized to [0, 1].

    High centrality = many modules depend on this one = mutations are risky.
    """
    deps = len(get_dependents(filepath))
    # Normalize against the max in the model
    model = get_self_model()
    if not model.dependency_graph:
        return 0.0
    max_deps = max(len(v) for v in model.dependency_graph.values()) or 1
    return round(deps / max_deps, 3)


# ── Idle scheduler entry point ───────────────────────────────────────────────

def refresh_self_model() -> None:
    """Background job: rebuild and save the self-model.

    Wired into idle_scheduler as a LIGHT job (typically <2s for ~300 modules).
    """
    try:
        get_self_model(force_rebuild=True)
    except Exception as e:
        logger.warning(f"self_model: refresh failed: {e}")
