"""JSONL append-only store for benchmark runs (Phase C.3, 2026-05-22).

Storage shape::

  workspace/benchmarks/
    runs.jsonl           # append-only, one BenchmarkRun per line

Why JSONL?

Same argument the code-intel store makes: append-only is a perfect fit
for an immutable event ledger, JSONL is mergeable / human-grep-able /
streamable. The benchmark suite is observational + advisory — every
row is an experiment we ran, never edited. Postgres would buy
indexing we don't need at this scale (low-hundreds of rows per day
at full saturation).

Re-evaluate if/when:

  * The runs file crosses 100 MB (≈100k rows). At that point the
    aggregator's full scan starts to feel slow.
  * Operators want SQL-like queries (rare — the React leaderboard
    pre-computes everything the dashboard needs).

Phase E.1 (2026-05-22): migrated to use :class:`app.utils.jsonl_ledger.JsonlLedger`
as the backing primitive. Public API unchanged — every existing caller
still imports the same module-level functions and gets the same shape
of result. The migration is here to demonstrate parity; future ledgers
can use ``JsonlLedger`` directly without re-implementing the surface.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterator, Optional

from app.benchmarks.models import BenchmarkRun
from app.utils.jsonl_ledger import JsonlLedger

logger = logging.getLogger(__name__)


# ── Path resolution ─────────────────────────────────────────────────


_base_dir_override: Optional[Path] = None


def _default_base_dir() -> Path:
    """Production default: ``workspace/benchmarks``. Honours the
    ``WORKSPACE_ROOT`` env override."""
    workspace = os.environ.get("WORKSPACE_ROOT", "workspace").strip()
    return Path(workspace) / "benchmarks"


def get_base_dir() -> Path:
    """Return the active base directory. Honours test override."""
    if _base_dir_override is not None:
        return _base_dir_override
    return _default_base_dir()


def runs_path() -> Path:
    """Where the ``runs.jsonl`` file lives."""
    return get_base_dir() / "runs.jsonl"


# ── Backing ledger ──────────────────────────────────────────────────


_ledger: JsonlLedger[BenchmarkRun] = JsonlLedger(
    name="benchmarks_runs",
    default_path=runs_path,
    rehydrate=BenchmarkRun.from_dict,
    serialise=lambda r: r.to_dict(),
    ts_field="ts",
)


# ── Public surface (unchanged) ──────────────────────────────────────


def append_run(run: BenchmarkRun) -> None:
    """Append one run to the JSONL file. Thread-safe via the ledger.

    Failure-isolated upstream — a disk error logs + raises, but the
    runner catches and continues with the rest of the catalog. Better
    to lose one row than have one bad row break the whole refresh
    pass.
    """
    _ledger.append(run)


def iter_runs() -> Iterator[BenchmarkRun]:
    """Yield every persisted run, in append order.

    Tolerates malformed rows — bad lines are skipped with a debug
    log, never raise. The aggregator uses this for stats; a single
    corrupted row shouldn't break the leaderboard.
    """
    yield from _ledger.iter_all()


def load_all() -> list[BenchmarkRun]:
    """Materialise every run. Convenience over :func:`iter_runs`."""
    return _ledger.load_all()


def stats() -> dict:
    """Summary for the operator surface — what's on disk right now.

    Returns ``{rows, bytes, last_ts}``. Cheap — delegates to the
    underlying ledger which does a single ``stat()`` + a one-pass
    line count.
    """
    return _ledger.stats()


# ── Test helpers ────────────────────────────────────────────────────


def reset_for_tests(base_dir: Optional[Path]) -> None:
    """Test helper — point the store at a tmp dir, or unset (None).

    Forwards to the backing ledger's ``reset_for_tests`` so tests
    that previously called this module-level helper continue to work
    exactly as before. The ledger needs the full path; we synthesise
    it from the base_dir + canonical filename.
    """
    global _base_dir_override
    _base_dir_override = base_dir
    if base_dir is None:
        _ledger.reset_for_tests(None)
    else:
        _ledger.reset_for_tests(base_dir / "runs.jsonl")


__all__ = [
    "append_run",
    "get_base_dir",
    "iter_runs",
    "load_all",
    "reset_for_tests",
    "runs_path",
    "stats",
]
