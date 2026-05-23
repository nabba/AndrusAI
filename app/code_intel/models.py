"""Code-intelligence data model (Phase 3 piece 1, 2026-05-20).

Pure-Python symbol model. No external deps (pyright / tree-sitter
land in a later sidecar upgrade). The AST-based indexer in
``indexer.py`` produces these dataclasses; the JSONL store in
``store.py`` persists them; the query API in ``query.py`` returns
them.

Symbols vs references
─────────────────────
* **Symbol**: a definition. ``def foo()`` or ``class Bar`` at
  ``file.py:42``. One per declaration site.
* **Reference**: a usage. ``foo()`` called at ``other.py:13``.
  Multiple per symbol; produced by walking the AST for ``Name``
  + ``Attribute`` nodes.

The model is deliberately narrow:
  * No type info (pyright lands in a sidecar later).
  * No call graph beyond text-name matching (true call graph
    needs sema analysis).
  * No cross-module resolution beyond filename — "where is the
    ``foo`` named?" answers all files that define a ``foo``.

This shape is enough for the high-value queries:
  * "where is X defined" → ``find_symbol(name="X")``
  * "where is X used" → ``find_references(name="X")``
  * "what functions call X" → ``find_callers(func_name="X")``

Future v2 hooks (when we add pyright/tree-sitter):
  * Type-resolved references that survive same-name collisions.
  * Multi-language support (TS/JS, Go, etc.).
  * Cross-file call-graph edges.
"""
from __future__ import annotations

import enum
from dataclasses import asdict, dataclass, field
from typing import Any


class SymbolKind(str, enum.Enum):
    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"           # function defined inside a class body
    ASYNC_FUNCTION = "async_function"
    ASYNC_METHOD = "async_method"


@dataclass(frozen=True)
class SymbolLocation:
    """One definition site. Immutable; identity = (file_path, name, lineno)."""

    name: str
    kind: SymbolKind
    file_path: str      # workspace-relative
    lineno: int         # 1-indexed
    end_lineno: int     # 1-indexed; same as lineno for single-line defs
    parent: str = ""    # parent class name for methods; empty for top-level
    docstring_first_line: str = ""   # first line of the docstring (≤120 chars)

    @property
    def fully_qualified(self) -> str:
        """``parent.name`` for methods; bare ``name`` for top-level
        symbols. Useful for disambiguating across files."""
        if self.parent:
            return f"{self.parent}.{self.name}"
        return self.name

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SymbolLocation":
        return cls(
            name=str(data.get("name", "")),
            kind=SymbolKind(data.get("kind", "function")),
            file_path=str(data.get("file_path", "")),
            lineno=int(data.get("lineno", 0)),
            end_lineno=int(data.get("end_lineno", 0)),
            parent=str(data.get("parent", "")),
            docstring_first_line=str(data.get("docstring_first_line", "")),
        )


@dataclass(frozen=True)
class ReferenceLocation:
    """One usage site. The ``in_function`` field carries the enclosing
    function/method name (for ``find_callers`` queries)."""

    name: str
    file_path: str      # workspace-relative
    lineno: int         # 1-indexed
    col_offset: int     # 0-indexed
    in_function: str = ""  # enclosing function name; "" for module-level
    in_class: str = ""     # enclosing class name; "" if not inside a class

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReferenceLocation":
        return cls(
            name=str(data.get("name", "")),
            file_path=str(data.get("file_path", "")),
            lineno=int(data.get("lineno", 0)),
            col_offset=int(data.get("col_offset", 0)),
            in_function=str(data.get("in_function", "")),
            in_class=str(data.get("in_class", "")),
        )


@dataclass
class IndexSnapshot:
    """The complete output of one indexer run.

    Returned by ``indexer.build_index`` and persisted by
    ``store.save_index``. Loadable via ``store.load_index``.
    """

    symbols: list[SymbolLocation] = field(default_factory=list)
    references: list[ReferenceLocation] = field(default_factory=list)
    indexed_files: list[str] = field(default_factory=list)
    indexed_at: str = ""   # ISO-8601 UTC

    def stats(self) -> dict[str, int]:
        """Quick summary for diagnostics + audit logs."""
        return {
            "symbols": len(self.symbols),
            "references": len(self.references),
            "indexed_files": len(self.indexed_files),
        }
