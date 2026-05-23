"""Code-intelligence subsystem (Phase 3 piece 1, 2026-05-20).

Pure-Python AST-based symbol indexer + JSONL store + query API.
Ships dormant — the indexer doesn't run on its own until the master
switch ``code_intel_enabled`` is flipped in runtime_settings.

Public surface
──────────────

  * :func:`build_index` — index a directory into an IndexSnapshot
  * :func:`save_index` / :func:`load_index` — persistence
  * :func:`find_symbol` / :func:`find_references` / :func:`find_callers`
    — query API
  * :class:`SymbolLocation` / :class:`ReferenceLocation` /
    :class:`SymbolKind` / :class:`IndexSnapshot` — data model

Composition
───────────

  * The autonomous executor's coder agent can call ``find_symbol``
    via a future ``code_intel_lookup`` tool to answer "where is X
    defined" without a grep walk.
  * The ``iterate_until_green`` loop can use ``find_callers`` to
    decide which test files to focus on when fixing a function.
  * The change-request validator could use the symbol index to
    detect "this CR moves a function — who calls it?" (future work).

Future v2 hooks
───────────────

  * Sidecar that runs pyright + tree-sitter for type-resolved
    references and multi-language support.
  * Postgres-backed index with (name, kind) and (file_path, lineno)
    indexes for sub-millisecond lookups at scale.
  * Cross-file call-graph edges with sema disambiguation.
"""
from __future__ import annotations

from app.code_intel.indexer import build_index
from app.code_intel.models import (
    IndexSnapshot,
    ReferenceLocation,
    SymbolKind,
    SymbolLocation,
)
from app.code_intel.query import (
    find_callers,
    find_module_deps,
    find_references,
    find_symbol,
    find_test_coverage,
    index_stats,
)
from app.code_intel.pyright_sidecar import (
    PyrightDiagnostic,
    PyrightReport,
    check_file,
    check_paths,
    is_available as pyright_is_available,
)
from app.code_intel.store import (
    is_built,
    load_index,
    save_index,
)

__all__ = [
    "IndexSnapshot",
    "PyrightDiagnostic",
    "PyrightReport",
    "ReferenceLocation",
    "SymbolKind",
    "SymbolLocation",
    "build_index",
    "check_file",
    "check_paths",
    "find_callers",
    "find_module_deps",
    "find_references",
    "find_symbol",
    "find_test_coverage",
    "index_stats",
    "is_built",
    "load_index",
    "pyright_is_available",
    "save_index",
]
