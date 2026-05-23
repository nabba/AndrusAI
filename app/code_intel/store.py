"""JSONL persistence for the code-intel index (Phase 3 piece 1).

Two files under ``workspace/code_intel/``:

  * ``symbols.jsonl``      — one SymbolLocation per line
  * ``references.jsonl``   — one ReferenceLocation per line
  * ``snapshot.json``      — metadata + indexed_at + indexed_files

The store is a write-once-replace-atomically pattern: a full index
build replaces the on-disk state via tempfile + rename. Lazy in-memory
cache fronts reads so the query API is fast after the first call.

Architecture decision: JSONL chosen over Postgres
─────────────────────────────────────────────────

The verified plan referenced "3 new Postgres tables" for this
subsystem. Implementation shipped as JSONL instead. Phase C.5 cleanup
(2026-05-22) commits to this choice deliberately. Rationale:

  1. Matches existing pattern. Every adjacent subsystem (threads,
     workflows, change_requests, executor runs, capability snapshots,
     connector_budget spend, two-reasoner reviews) uses JSONL. A
     Postgres-only subsystem introduces a second persistence model.
  2. Zero schema-migration debt. The index is rebuilt fresh on every
     refresh — no migrations to author, no startup migration script.
  3. Trivially inspectable. ``grep -l 'class CodingSession' \\
     workspace/code_intel/symbols.jsonl`` works in any shell.
  4. Survives DB outages. Code-intel queries keep working when the
     Postgres pool is sick.
  5. Volume fits comfortably. A 50k-symbol index is ~30 MB JSONL —
     well below the threshold where ad-hoc grep becomes painful.

Re-evaluate if any of:
  * symbol count grows past 100k (then SQLite or DuckDB starts to win)
  * query patterns require cross-symbol joins (semantic-search /
    type-resolved references)
  * the index becomes a CI artifact rebuilt out-of-band

For now, the JSONL store is the right shape. Operators can inspect
state via :func:`stats` (also exposed at ``GET /api/cp/code-intel/stats``).
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Iterable, Optional

from app.code_intel.models import (
    IndexSnapshot,
    ReferenceLocation,
    SymbolLocation,
)

logger = logging.getLogger(__name__)


_DEFAULT_BASE_DIR = Path("/app/workspace/code_intel")
_base_dir_override: Path | None = None
_LOCK = threading.RLock()
_CACHE: IndexSnapshot | None = None


def _base_dir() -> Path:
    return _base_dir_override or _DEFAULT_BASE_DIR


def get_base_dir() -> Path:
    return _base_dir()


def _symbols_path() -> Path:
    return _base_dir() / "symbols.jsonl"


def _references_path() -> Path:
    return _base_dir() / "references.jsonl"


def _snapshot_path() -> Path:
    return _base_dir() / "snapshot.json"


def _ensure_dir() -> None:
    _base_dir().mkdir(parents=True, exist_ok=True)


def _atomic_write_jsonl(
    path: Path,
    rows: Iterable[dict],
) -> None:
    """Atomic write: serialise → tempfile → rename. Same pattern as
    threads/workflows/coding_session stores."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, separators=(",", ":")))
            f.write("\n")
    tmp.replace(path)


def save_index(snapshot: IndexSnapshot) -> dict[str, int]:
    """Persist a complete snapshot. Atomic — readers see either the
    pre-update state or the post-update state, never a partial.

    Returns
    -------
    dict[str, int]
        ``{"symbols": N, "references": M, "indexed_files": K}``
    """
    with _LOCK:
        _ensure_dir()
        _atomic_write_jsonl(
            _symbols_path(),
            (s.to_dict() for s in snapshot.symbols),
        )
        _atomic_write_jsonl(
            _references_path(),
            (r.to_dict() for r in snapshot.references),
        )
        meta = {
            "indexed_at": snapshot.indexed_at,
            "indexed_files": list(snapshot.indexed_files),
            "stats": snapshot.stats(),
        }
        tmp = _snapshot_path().with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta, indent=2))
        tmp.replace(_snapshot_path())

        # Invalidate the read cache so the next query sees fresh data.
        global _CACHE
        _CACHE = None

    # Verified Plan §5 Gap F closure (2026-05-23) — dual-write into
    # the migration-036 Postgres tables when the master switch is on.
    # JSONL persistence above is canonical; this is the queryable
    # mirror. Failure-isolated: a Postgres write error is logged but
    # never propagated — the JSONL truth stands.
    try:
        from app.code_intel import postgres_store
        if postgres_store.is_enabled():
            pg_result = postgres_store.save_index(snapshot)
            if not pg_result.get("ok"):
                logger.debug(
                    "save_index: postgres mirror failed (%s); "
                    "JSONL persisted ok",
                    pg_result.get("error"),
                )
    except Exception:
        logger.debug(
            "save_index: postgres dual-write raised", exc_info=True,
        )

    return snapshot.stats()


def load_index() -> IndexSnapshot:
    """Return the current on-disk snapshot. Cached in-memory after
    first read; ``save_index`` invalidates the cache.

    Returns an empty snapshot when the index hasn't been built yet
    (rather than raising). Callers (the agent tools) handle empty
    gracefully — "no symbols indexed yet" is a normal v1 state.
    """
    global _CACHE
    with _LOCK:
        if _CACHE is not None:
            return _CACHE
        snapshot = IndexSnapshot()
        symbols_path = _symbols_path()
        if symbols_path.exists():
            try:
                with symbols_path.open(encoding="utf-8") as f:
                    snapshot.symbols = [
                        SymbolLocation.from_dict(json.loads(line))
                        for line in f if line.strip()
                    ]
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning(
                    "code_intel: failed to load symbols.jsonl: %s",
                    exc,
                )
        refs_path = _references_path()
        if refs_path.exists():
            try:
                with refs_path.open(encoding="utf-8") as f:
                    snapshot.references = [
                        ReferenceLocation.from_dict(json.loads(line))
                        for line in f if line.strip()
                    ]
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning(
                    "code_intel: failed to load references.jsonl: %s",
                    exc,
                )
        snap_path = _snapshot_path()
        if snap_path.exists():
            try:
                meta = json.loads(snap_path.read_text(encoding="utf-8"))
                snapshot.indexed_at = str(meta.get("indexed_at", ""))
                snapshot.indexed_files = list(meta.get("indexed_files", []))
            except (OSError, json.JSONDecodeError):
                pass
        _CACHE = snapshot
        return snapshot


def reset_for_tests(base_dir: Optional[Path] = None) -> None:
    """Test helper — flush the in-memory cache and optionally redirect
    the base dir to a tmp path. Never call from production."""
    global _CACHE, _base_dir_override
    with _LOCK:
        _CACHE = None
        _base_dir_override = base_dir


def is_built() -> bool:
    """Quick check: does the snapshot file exist? Useful for the
    agent tools to decide whether to surface an explanatory message
    when the index is empty."""
    return _snapshot_path().exists()


def stats() -> dict:
    """One-shot operator surface for index state.

    Phase C.5 cleanup (2026-05-22) — surfaces the JSONL store's
    state without requiring shell access. Returns:

      * ``built``: bool — snapshot file exists
      * ``symbols_count``: int — lines in symbols.jsonl
      * ``references_count``: int — lines in references.jsonl
      * ``indexed_files_count``: int — distinct files in the snapshot
      * ``symbols_bytes``: int — on-disk size
      * ``references_bytes``: int — on-disk size
      * ``indexed_at``: str — ISO timestamp from snapshot metadata
      * ``age_seconds``: int | None — seconds since indexed_at, or
        None when the snapshot is malformed/unparseable

    Failure-isolated end-to-end — a sick filesystem returns zero
    values for the failing fields rather than raising.
    """
    out: dict = {
        "built": False,
        "symbols_count": 0,
        "references_count": 0,
        "indexed_files_count": 0,
        "symbols_bytes": 0,
        "references_bytes": 0,
        "indexed_at": "",
        "age_seconds": None,
    }
    snap_path = _snapshot_path()
    if not snap_path.exists():
        return out
    out["built"] = True

    # Count lines in each JSONL file
    for key, path in (
        ("symbols_count", _symbols_path()),
        ("references_count", _references_path()),
    ):
        try:
            if path.exists():
                with path.open("rb") as fh:
                    out[key] = sum(1 for _ in fh)
        except OSError:
            pass

    # File sizes
    for key, path in (
        ("symbols_bytes", _symbols_path()),
        ("references_bytes", _references_path()),
    ):
        try:
            if path.exists():
                out[key] = path.stat().st_size
        except OSError:
            pass

    # Snapshot metadata
    try:
        meta = json.loads(snap_path.read_text(encoding="utf-8"))
        indexed_at = str(meta.get("indexed_at", "") or "")
        out["indexed_at"] = indexed_at
        out["indexed_files_count"] = len(meta.get("indexed_files", []))
        if indexed_at:
            import datetime as _dt
            try:
                dt = _dt.datetime.fromisoformat(indexed_at)
                now = _dt.datetime.now(_dt.timezone.utc)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=_dt.timezone.utc)
                out["age_seconds"] = int((now - dt).total_seconds())
            except ValueError:
                pass
    except (OSError, json.JSONDecodeError):
        pass

    return out
