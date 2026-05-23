"""AST-based code indexer (Phase 3 piece 1, 2026-05-20).

Walks a root directory, parses every ``.py`` file via the stdlib
``ast`` module, and produces an :class:`IndexSnapshot` with symbol
definitions + reference sites.

Design choices:

* **Pure stdlib.** No pyright, no tree-sitter, no LSP. The indexer
  runs in the same Python process that everything else does. This
  Phase 3 piece 1 deliberately defers the heavier toolchain — the
  AST-based version is enough for the high-value queries and lets
  us ship the query API + agent tools without external deps.
  Trade-off: no type-resolved references, no cross-file call-graph
  edges beyond text-name matching. Add those when the volume of
  same-name collisions warrants it.

* **Bounded.** ``build_index`` honors ``MAX_FILES`` and
  ``MAX_FILE_BYTES`` to avoid runaway memory on monorepos. The
  current defaults (50000 files, 1 MB per file) are far above
  this repo's needs; tighten if needed.

* **Failure-isolated.** A parse error on one file produces a
  warning log and a skipped file; the index for other files lands.
  Pinned by ``test_indexer_skips_unparseable_file``.

* **Skip rules.** ``_should_skip`` returns True for ``.venv/``,
  ``node_modules/``, ``__pycache__/``, ``.git/``, ``site-packages/``,
  and any path under ``.claude-backup-*``. Operators add more by
  passing ``extra_skip_dirs`` to ``build_index``.
"""
from __future__ import annotations

import ast
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from app.code_intel.models import (
    IndexSnapshot,
    ReferenceLocation,
    SymbolKind,
    SymbolLocation,
)

logger = logging.getLogger(__name__)


# Bounds — defend against monorepo blowups.
MAX_FILES = 50_000
MAX_FILE_BYTES = 1_048_576  # 1 MB; bigger files are usually fixtures or vendored

_SKIP_DIRS = frozenset({
    ".venv", "venv", "env",
    "node_modules", "site-packages", "dist-packages",
    "__pycache__", ".git", ".pytest_cache", ".mypy_cache",
    ".tox", ".eggs", "build", "dist", "htmlcov",
})


def _should_skip(path: Path, extra_skip_dirs: frozenset[str]) -> bool:
    """True when ``path`` is inside a directory we shouldn't index."""
    for part in path.parts:
        if part in _SKIP_DIRS or part in extra_skip_dirs:
            return True
        # Pattern: ``.something-backup-...`` directories.
        if part.startswith(".") and "backup" in part.lower():
            return True
    return False


def _docstring_first_line(node: ast.AST) -> str:
    """Extract the first line of a function/class docstring, capped at
    120 chars. Returns "" when no docstring is present."""
    try:
        doc = ast.get_docstring(node)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    if not doc:
        return ""
    first = doc.splitlines()[0].strip()
    if len(first) > 120:
        first = first[:117] + "..."
    return first


class _SymbolVisitor(ast.NodeVisitor):
    """Walks the AST and collects symbol definitions + references.

    The visitor maintains two stacks:
      * ``class_stack`` — currently-open class names (for parent
        attribution on methods).
      * ``func_stack`` — currently-open function names (for
        ``in_function`` attribution on references).
    """

    def __init__(self, file_path: str) -> None:
        self.file_path = file_path
        self.symbols: list[SymbolLocation] = []
        self.references: list[ReferenceLocation] = []
        self.class_stack: list[str] = []
        self.func_stack: list[str] = []

    # ── Definitions ────────────────────────────────────────────────

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record_function(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._record_function(node, is_async=True)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.symbols.append(SymbolLocation(
            name=node.name,
            kind=SymbolKind.CLASS,
            file_path=self.file_path,
            lineno=node.lineno,
            end_lineno=getattr(node, "end_lineno", node.lineno),
            parent="",
            docstring_first_line=_docstring_first_line(node),
        ))
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()

    def _record_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        is_async: bool,
    ) -> None:
        parent = self.class_stack[-1] if self.class_stack else ""
        kind: SymbolKind
        if parent:
            kind = (
                SymbolKind.ASYNC_METHOD if is_async else SymbolKind.METHOD
            )
        else:
            kind = (
                SymbolKind.ASYNC_FUNCTION if is_async
                else SymbolKind.FUNCTION
            )
        self.symbols.append(SymbolLocation(
            name=node.name,
            kind=kind,
            file_path=self.file_path,
            lineno=node.lineno,
            end_lineno=getattr(node, "end_lineno", node.lineno),
            parent=parent,
            docstring_first_line=_docstring_first_line(node),
        ))
        self.func_stack.append(node.name)
        self.generic_visit(node)
        self.func_stack.pop()

    # ── References (text-name matching only) ──────────────────────

    def visit_Name(self, node: ast.Name) -> None:
        # Skip stores (``x = …``) — only loads (``… = x``, ``x()``) are
        # interesting for "where is X used".
        if isinstance(node.ctx, ast.Load):
            self.references.append(ReferenceLocation(
                name=node.id,
                file_path=self.file_path,
                lineno=node.lineno,
                col_offset=node.col_offset,
                in_function=(
                    self.func_stack[-1] if self.func_stack else ""
                ),
                in_class=(
                    self.class_stack[-1] if self.class_stack else ""
                ),
            ))

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # ``foo.bar`` references — record the attr name. Lets
        # ``find_references("save")`` find ``store.save(...)`` calls.
        if isinstance(node.ctx, ast.Load):
            self.references.append(ReferenceLocation(
                name=node.attr,
                file_path=self.file_path,
                lineno=node.lineno,
                col_offset=node.col_offset,
                in_function=(
                    self.func_stack[-1] if self.func_stack else ""
                ),
                in_class=(
                    self.class_stack[-1] if self.class_stack else ""
                ),
            ))
        self.generic_visit(node)


def _index_file(
    file_path: Path,
    *,
    relative_to: Path,
) -> tuple[list[SymbolLocation], list[ReferenceLocation]]:
    """Parse one file and extract its symbols + references. Returns
    ``([], [])`` on any IO / parse error (and logs at DEBUG)."""
    try:
        if file_path.stat().st_size > MAX_FILE_BYTES:
            logger.debug(
                "code_intel: skipping oversized file %s", file_path,
            )
            return [], []
    except OSError:
        return [], []

    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError) as exc:
        logger.debug("code_intel: read failed for %s: %s", file_path, exc)
        return [], []

    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError as exc:
        logger.debug(
            "code_intel: parse failed for %s: %s", file_path, exc,
        )
        return [], []

    try:
        rel = file_path.relative_to(relative_to)
    except ValueError:
        rel = file_path
    rel_str = str(rel).replace("\\", "/")

    visitor = _SymbolVisitor(file_path=rel_str)
    visitor.visit(tree)
    return visitor.symbols, visitor.references


def build_index(
    *,
    root: Path | str,
    extra_skip_dirs: Iterable[str] = (),
    file_glob: str = "**/*.py",
    max_files: Optional[int] = None,
) -> IndexSnapshot:
    """Walk ``root``, index every Python file, return an IndexSnapshot.

    Parameters
    ----------
    root
        Absolute or relative path to the directory to index. Paths
        in the returned snapshot are stored RELATIVE to this root so
        the index is portable across host paths.
    extra_skip_dirs
        Additional directory names to skip (added to ``_SKIP_DIRS``).
    file_glob
        Pattern to match. Default ``**/*.py`` for Python only.
    max_files
        Cap on the number of files indexed. Defaults to ``MAX_FILES``;
        tighten for tests.

    Returns
    -------
    IndexSnapshot
        Populated with symbols + references + indexed_files +
        indexed_at timestamp.
    """
    root_path = Path(root).resolve()
    if not root_path.exists():
        raise ValueError(f"build_index: root does not exist: {root_path}")
    if not root_path.is_dir():
        raise ValueError(f"build_index: root is not a directory: {root_path}")

    cap = max_files if max_files is not None else MAX_FILES
    skip = frozenset(extra_skip_dirs)
    snapshot = IndexSnapshot(
        indexed_at=datetime.now(timezone.utc).isoformat(),
    )

    n_scanned = 0
    for file_path in root_path.glob(file_glob):
        if not file_path.is_file():
            continue
        if _should_skip(file_path.relative_to(root_path), skip):
            continue
        n_scanned += 1
        if n_scanned > cap:
            logger.info(
                "code_intel: hit max_files cap (%d); truncating index",
                cap,
            )
            break
        symbols, refs = _index_file(file_path, relative_to=root_path)
        if not symbols and not refs:
            continue
        snapshot.symbols.extend(symbols)
        snapshot.references.extend(refs)
        rel = file_path.relative_to(root_path)
        snapshot.indexed_files.append(str(rel).replace("\\", "/"))

    return snapshot
