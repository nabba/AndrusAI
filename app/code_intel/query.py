"""Query API for the code-intel index (Phase 3 piece 1, 2026-05-20).

Three v1 queries:

  * :func:`find_symbol(name, kind?)` — definitions matching ``name``.
  * :func:`find_references(name)` — usage sites of ``name``.
  * :func:`find_callers(func_name)` — caller-function symbols for
    every reference to ``func_name``.

All three are pure-function over the snapshot returned by
``store.load_index()``. The snapshot is cached in-memory after the
first read so query calls are O(1) after warm-up.

For v1 the matching is **text-only** — ``find_references("save")``
returns every reference whose name is ``save`` regardless of which
``save`` (module function, class method, attribute access). The
caller can disambiguate via the returned ``file_path`` / ``in_class``
fields. Future v2 with pyright will do type-resolved matching.
"""
from __future__ import annotations

from typing import Optional

from app.code_intel.models import (
    ReferenceLocation,
    SymbolKind,
    SymbolLocation,
)
from app.code_intel.store import load_index


# Practical caps so an unconstrained query on a large index doesn't
# return tens of thousands of rows. Callers wanting more pass an
# explicit ``limit`` argument.
_DEFAULT_LIMIT = 200


def find_symbol(
    name: str,
    *,
    kind: Optional[SymbolKind] = None,
    file_prefix: Optional[str] = None,
    limit: int = _DEFAULT_LIMIT,
) -> list[SymbolLocation]:
    """Return every definition site whose name equals ``name``.

    Parameters
    ----------
    name
        Exact match (case-sensitive). The current v1 doesn't do
        substring or glob — operators / agents use ``file_manager``
        with grep for fuzzy lookups.
    kind
        Restrict to one ``SymbolKind`` (FUNCTION / CLASS / METHOD /
        ASYNC_FUNCTION / ASYNC_METHOD). ``None`` returns all kinds.
    file_prefix
        Restrict to definitions in files whose path starts with this
        prefix. Useful for "where in ``app/agents/`` is ``handle``
        defined" queries.
    limit
        Cap on returned rows. Default 200.

    Returns
    -------
    list[SymbolLocation]
        Sorted by ``(file_path, lineno)`` for deterministic output.
    """
    snapshot = load_index()
    out: list[SymbolLocation] = []
    for s in snapshot.symbols:
        if s.name != name:
            continue
        if kind is not None and s.kind is not kind:
            continue
        if file_prefix and not s.file_path.startswith(file_prefix):
            continue
        out.append(s)
        if len(out) >= limit:
            break
    out.sort(key=lambda x: (x.file_path, x.lineno))
    return out


def find_references(
    name: str,
    *,
    file_prefix: Optional[str] = None,
    limit: int = _DEFAULT_LIMIT,
) -> list[ReferenceLocation]:
    """Return every reference site whose name equals ``name``.

    Includes both ``Name`` references (``foo()``) and ``Attribute``
    references (``module.foo``). Filtering by file_prefix lets the
    caller narrow to a subtree.
    """
    snapshot = load_index()
    out: list[ReferenceLocation] = []
    for r in snapshot.references:
        if r.name != name:
            continue
        if file_prefix and not r.file_path.startswith(file_prefix):
            continue
        out.append(r)
        if len(out) >= limit:
            break
    out.sort(key=lambda x: (x.file_path, x.lineno))
    return out


def find_callers(
    func_name: str,
    *,
    file_prefix: Optional[str] = None,
    limit: int = _DEFAULT_LIMIT,
) -> list[SymbolLocation]:
    """Return the enclosing function/method of every reference to
    ``func_name``. Deduplicated by ``(file_path, function_name)``.

    Best-effort: matches the reference's ``in_function`` field against
    the symbol table. A reference inside a lambda or at module level
    is omitted (no caller function to attribute).
    """
    snapshot = load_index()
    # Build (file_path → name → SymbolLocation) lookup for the symbols
    # we might attribute callers to. Method names collide across
    # classes, so we key by (file, name, parent) when available.
    by_loc: dict[tuple[str, str, str], SymbolLocation] = {}
    for s in snapshot.symbols:
        if s.kind in (
            SymbolKind.FUNCTION,
            SymbolKind.ASYNC_FUNCTION,
            SymbolKind.METHOD,
            SymbolKind.ASYNC_METHOD,
        ):
            by_loc[(s.file_path, s.name, s.parent)] = s

    seen: set[tuple[str, str, str]] = set()
    out: list[SymbolLocation] = []
    for r in snapshot.references:
        if r.name != func_name:
            continue
        if not r.in_function:
            continue  # module-level call — no caller-function symbol
        if file_prefix and not r.file_path.startswith(file_prefix):
            continue
        key = (r.file_path, r.in_function, r.in_class)
        if key in seen:
            continue
        seen.add(key)
        sym = by_loc.get((r.file_path, r.in_function, r.in_class))
        if sym is None:
            # Try parent-less lookup (in_class may be unknown for
            # nested funcs)
            sym = by_loc.get((r.file_path, r.in_function, ""))
        if sym is not None:
            out.append(sym)
            if len(out) >= limit:
                break
    out.sort(key=lambda x: (x.file_path, x.lineno))
    return out


def index_stats() -> dict[str, int]:
    """Quick summary for diagnostics — what's in the index right
    now."""
    snapshot = load_index()
    return snapshot.stats()


# ── Phase C.2 v2 queries (2026-05-22) ────────────────────────────────


def find_test_coverage(
    name: str,
    *,
    test_root: str = "tests/",
    limit: int = _DEFAULT_LIMIT,
) -> list[ReferenceLocation]:
    """Return references to ``name`` that live under ``test_root``.

    Phase C.2 (2026-05-22). Composes :func:`find_references` with a
    file_path prefix filter so the agent can ask "which tests
    exercise this symbol?" — answers "blast radius if I break this"
    from the test side.

    The default ``test_root="tests/"`` matches the project layout;
    callers can override for non-standard test trees.
    """
    refs = find_references(name, file_prefix=test_root, limit=limit)
    return refs


def find_module_deps(file_path: str) -> list[str]:
    """Return every module ``file_path`` imports.

    Phase C.2 (2026-05-22). AST-walks the file's import section,
    collects every ``import X`` / ``from X import ...`` module name,
    returns them sorted + deduplicated.

    This is a fresh AST parse of the file on disk — does NOT consult
    the JSONL index. The index doesn't currently track imports;
    rather than adding a new schema, we re-parse on demand. The
    operation is cheap (one file, syntactic-only AST walk).

    Returns an empty list when the file doesn't exist or can't be
    parsed. Failure-isolated — callers can treat empty as "no deps
    info available" without distinguishing why.
    """
    import ast
    from pathlib import Path

    try:
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return []
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []

    deps: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name:
                    deps.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            # ``from X import Y`` records X. Relative imports (level>0)
            # record the relative form like ".sibling" — the agent
            # can decide what to make of it.
            if node.module:
                if node.level:
                    deps.add("." * node.level + node.module)
                else:
                    deps.add(node.module)
            elif node.level:
                # ``from . import sibling`` — record the bare relative
                deps.add("." * node.level)
    return sorted(deps)
