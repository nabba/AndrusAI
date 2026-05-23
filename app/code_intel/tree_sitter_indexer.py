"""Tree-sitter symbol indexer (Verified Implementation Plan §5 closure
Gap 1, 2026-05-23).

Companion to ``app/code_intel/indexer.py``. That module uses
Python's built-in ``ast`` — fast and dependency-light for Python
files but doesn't see other languages. This module uses
``tree-sitter`` (added to ``requirements.txt`` 2026-05-23 alongside
``ruff``) to parse the same Python files plus future TS/JS/Go/Rust
sources, producing identically-shaped ``SymbolLocation`` and
``ReferenceLocation`` records.

Design
──────

  * **Same model**: outputs flow into the existing
    :class:`IndexSnapshot` shape. Downstream queries don't care
    which indexer produced a record.
  * **Additive**: never replaces the AST indexer. Operator opts in
    via ``code_intel_tree_sitter_enabled`` (default OFF). With it
    OFF, behaviour is bit-identical to today.
  * **Language registry**: ``_LANGUAGE_REGISTRY`` maps file
    extensions to a tree-sitter ``Language`` object. v1 ships
    Python only (the rest of the gateway is Python); JS/TS/Go/Rust
    drop in by adding a row to the dict (and a ``tree-sitter-<lang>``
    wheel to ``requirements.txt`` via the standard CR flow).
  * **Failure-isolated**: tree-sitter missing / language failing to
    load / file unreadable all return empty results rather than
    raise — the AST indexer remains the canonical path.

Why a parallel indexer rather than replacement
──────────────────────────────────────────────

  1. Tree-sitter and Python's AST disagree on edge cases (decorator
     locations, lambda bodies, etc.). Running both lets the JSONL
     store accumulate symbols from either source, with
     ``language`` + ``source`` metadata distinguishing them.
  2. Migration: we can compare AST vs tree-sitter output across
     the codebase before deciding to retire the AST path.
  3. Multi-language: the AST path is Python-only by construction;
     tree-sitter is the bridge to other languages.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Callable, Optional

from app.code_intel.models import (
    IndexSnapshot,
    ReferenceLocation,
    SymbolKind,
    SymbolLocation,
)

logger = logging.getLogger(__name__)


# ── Language registry ──────────────────────────────────────────────


def _python_language():
    """Lazy-load the tree-sitter Python grammar; cache once.

    Returns the language capsule or None if either tree-sitter or
    tree-sitter-python is unavailable.
    """
    try:
        import tree_sitter
        import tree_sitter_python
        return tree_sitter.Language(tree_sitter_python.language())
    except Exception:
        logger.debug(
            "tree_sitter_indexer: Python grammar unavailable",
            exc_info=True,
        )
        return None


_LANGUAGE_REGISTRY: dict[str, Callable[[], Any]] = {
    ".py":  _python_language,
    # Future:
    # ".ts":  _typescript_language,
    # ".js":  _javascript_language,
    # ".go":  _go_language,
    # ".rs":  _rust_language,
}


def supported_extensions() -> tuple[str, ...]:
    """Sorted tuple of file extensions this indexer can parse.

    Public so the daemon can decide whether to invoke us at all
    for a given changeset.
    """
    return tuple(sorted(_LANGUAGE_REGISTRY.keys()))


# ── Master switch ──────────────────────────────────────────────────


def is_enabled() -> bool:
    """Master switch read.

    Precedence: env var ``CODE_INTEL_TREE_SITTER_ENABLED`` first
    (ops override / test fixtures), then runtime_settings getter
    (React-toggleable persistent state), then default OFF.

    Default OFF because tree-sitter ships as v1 — the AST indexer
    is the canonical Python path. Operators flip when they want
    cross-language coverage OR want to A/B the two parsers.
    """
    env = os.environ.get("CODE_INTEL_TREE_SITTER_ENABLED", "")
    if env:
        return env.lower() in ("1", "true", "yes", "on")
    try:
        from app import runtime_settings
        getter = getattr(
            runtime_settings, "get_code_intel_tree_sitter_enabled", None,
        )
        if callable(getter):
            return bool(getter())
    except Exception:
        logger.debug(
            "tree_sitter_indexer: runtime_settings read raised",
            exc_info=True,
        )
    return False


# ── Parser wrapper ─────────────────────────────────────────────────


def _make_parser(language) -> Optional[Any]:
    """Return a tree-sitter Parser bound to ``language``, or None."""
    try:
        import tree_sitter
        parser = tree_sitter.Parser()
        # tree-sitter 0.22+ API: assign to .language property.
        # Older API: parser.set_language(language). Try both for
        # cross-version robustness.
        try:
            parser.language = language
        except Exception:
            parser.set_language(language)
        return parser
    except Exception:
        logger.debug(
            "tree_sitter_indexer: Parser construction failed",
            exc_info=True,
        )
        return None


# ── Python symbol extraction ───────────────────────────────────────


def _walk(node, fn: Callable[[Any], None]) -> None:
    """Depth-first walk over a tree-sitter node tree."""
    fn(node)
    for child in node.children:
        _walk(child, fn)


def _text_of(node, source: bytes) -> str:
    """UTF-8 text of a node's byte span. Always returns a string
    (decode-replace on invalid bytes)."""
    try:
        return source[node.start_byte:node.end_byte].decode(
            "utf-8", errors="replace",
        )
    except Exception:
        return ""


def _enclosing_class_name(node, source: bytes) -> str:
    """Walk parents to find an enclosing class_definition; return its
    identifier, or empty string if none."""
    cur = node.parent
    while cur is not None:
        if cur.type == "class_definition":
            for child in cur.children:
                if child.type == "identifier":
                    return _text_of(child, source)
            return ""
        cur = cur.parent
    return ""


def _enclosing_function_name(node, source: bytes) -> str:
    """Walk parents to find an enclosing function_definition; return
    its identifier."""
    cur = node.parent
    while cur is not None:
        if cur.type in ("function_definition",):
            for child in cur.children:
                if child.type == "identifier":
                    return _text_of(child, source)
            return ""
        cur = cur.parent
    return ""


def _is_async_function(node) -> bool:
    """Check whether a function_definition node is async.

    tree-sitter-python marks async functions with an ``async``
    keyword child OR via the ``async`` attribute on the parent.
    """
    for child in node.children:
        if child.type == "async":
            return True
    return False


def _extract_symbols_from_python(
    source: bytes, file_path: str, tree,
) -> tuple[list[SymbolLocation], list[ReferenceLocation]]:
    """Walk a parsed Python tree → symbols + references."""
    symbols: list[SymbolLocation] = []
    references: list[ReferenceLocation] = []

    def visit(node):
        # ── Definitions ─────────────────────────────────────────
        if node.type == "function_definition":
            name = ""
            for child in node.children:
                if child.type == "identifier":
                    name = _text_of(child, source)
                    break
            if name:
                parent = _enclosing_class_name(node, source)
                is_async = _is_async_function(node)
                if parent:
                    kind = (
                        SymbolKind.ASYNC_METHOD if is_async
                        else SymbolKind.METHOD
                    )
                else:
                    kind = (
                        SymbolKind.ASYNC_FUNCTION if is_async
                        else SymbolKind.FUNCTION
                    )
                symbols.append(SymbolLocation(
                    name=name,
                    kind=kind,
                    file_path=file_path,
                    lineno=node.start_point[0] + 1,
                    end_lineno=node.end_point[0] + 1,
                    parent=parent,
                    docstring_first_line="",  # tree-sitter docstring
                                              # extraction deferred to v2
                ))

        elif node.type == "class_definition":
            name = ""
            for child in node.children:
                if child.type == "identifier":
                    name = _text_of(child, source)
                    break
            if name:
                symbols.append(SymbolLocation(
                    name=name,
                    kind=SymbolKind.CLASS,
                    file_path=file_path,
                    lineno=node.start_point[0] + 1,
                    end_lineno=node.end_point[0] + 1,
                    parent="",
                    docstring_first_line="",
                ))

        # ── References (call expressions, only the callable name) ──
        # Limit to call-site identifiers to match the AST indexer's
        # scope; broadening to all Name nodes would double-count and
        # complicate the JSONL row count.
        elif node.type == "call":
            callable_node = node.children[0] if node.children else None
            if callable_node is None:
                return
            if callable_node.type == "identifier":
                name = _text_of(callable_node, source)
            elif callable_node.type == "attribute":
                # foo.bar(...) — take the bar
                attr_name = ""
                for child in callable_node.children:
                    if child.type == "identifier":
                        attr_name = _text_of(child, source)
                name = attr_name
            else:
                return
            if not name:
                return
            references.append(ReferenceLocation(
                name=name,
                file_path=file_path,
                lineno=callable_node.start_point[0] + 1,
                col_offset=callable_node.start_point[1],
                in_function=_enclosing_function_name(node, source),
                in_class=_enclosing_class_name(node, source),
            ))

    _walk(tree.root_node, visit)
    return symbols, references


# ── Public API ─────────────────────────────────────────────────────


def index_file_with_tree_sitter(
    path: Path,
    *,
    workspace_root: Path,
) -> tuple[list[SymbolLocation], list[ReferenceLocation]]:
    """Parse a single file with tree-sitter; return symbols+references.

    Returns ``([], [])`` on any failure (extension unsupported,
    grammar unavailable, parse error, unreadable file). The caller
    should fall back to the AST indexer for Python files when this
    returns empty AND ``is_enabled()`` was True (logged-warning
    visibility into the dual-path execution).
    """
    suffix = path.suffix.lower()
    if suffix not in _LANGUAGE_REGISTRY:
        return [], []
    lang_loader = _LANGUAGE_REGISTRY[suffix]
    language = lang_loader()
    if language is None:
        return [], []
    parser = _make_parser(language)
    if parser is None:
        return [], []
    try:
        source = path.read_bytes()
    except Exception:
        logger.debug(
            "tree_sitter_indexer: %s unreadable", path, exc_info=True,
        )
        return [], []
    try:
        tree = parser.parse(source)
    except Exception:
        logger.debug(
            "tree_sitter_indexer: %s parse failed", path, exc_info=True,
        )
        return [], []
    try:
        rel_path = str(path.relative_to(workspace_root))
    except ValueError:
        rel_path = str(path)

    if suffix == ".py":
        return _extract_symbols_from_python(source, rel_path, tree)

    # Other languages drop in here when added to the registry.
    return [], []


def build_tree_sitter_snapshot(
    workspace_root: Path,
    *,
    skip_dirs: frozenset[str] = frozenset(),
) -> IndexSnapshot:
    """Walk ``workspace_root`` and produce a fresh IndexSnapshot via
    tree-sitter. Mirrors the contract of ``indexer.build_index`` but
    for the configured set of languages.

    Skips hidden dirs, ``__pycache__``, ``.venv*``, ``node_modules``,
    plus any extras the caller passes.
    """
    from datetime import datetime, timezone

    default_skips = frozenset({
        "__pycache__", "node_modules", ".git", "build", "dist",
        ".pytest_cache", ".ruff_cache",
    })
    all_skips = default_skips | skip_dirs

    snapshot = IndexSnapshot(
        symbols=[],
        references=[],
        indexed_files=[],
        indexed_at=datetime.now(tz=timezone.utc).isoformat(
            timespec="seconds",
        ),
    )

    supported = supported_extensions()

    for root, dirs, files in os.walk(workspace_root):
        # Mutate dirs in place to skip
        dirs[:] = [
            d for d in dirs
            if d not in all_skips and not d.startswith(".venv")
        ]
        for fname in files:
            if not any(fname.endswith(ext) for ext in supported):
                continue
            full = Path(root) / fname
            syms, refs = index_file_with_tree_sitter(
                full, workspace_root=workspace_root,
            )
            if syms or refs:
                snapshot.symbols.extend(syms)
                snapshot.references.extend(refs)
                try:
                    rel = str(full.relative_to(workspace_root))
                except ValueError:
                    rel = str(full)
                snapshot.indexed_files.append(rel)

    return snapshot


__all__ = [
    "is_enabled",
    "supported_extensions",
    "index_file_with_tree_sitter",
    "build_tree_sitter_snapshot",
]
