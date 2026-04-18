"""disk_cache.py — SQLite-backed L2 cache for embeddings & sub-queries.

Stage 3.1 + 3.2 of the speed-upgrade plan. Survives container restart so
the first-query cold penalty disappears. In-proc LRU stays primary (sub-ms);
sqlite is only consulted on LRU miss (~0.2-1 ms per hit vs ~15 ms for an
Ollama embed round-trip).

Two tables share one file:
  embed_cache (sha256 TEXT PRIMARY KEY, dim INT, vec BLOB, created_at REAL)
  decomp_cache (query_sha256 TEXT PRIMARY KEY, subqueries_json TEXT, created_at REAL)

Thread-safe via a per-connection lock. No concurrent writers needed — the
gateway is single-process; Ollama workers read the same file.

Caps:
  * embed_cache: soft cap 100_000 rows; weekly VACUUM via prune().
  * decomp_cache: soft cap 10_000 rows.
Oldest-entry-first eviction on overflow. Safe to delete the file — it
rebuilds itself from the Ollama/LLM source of truth on next miss.
"""
from __future__ import annotations

import array
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default location is inside the persistent workspace volume.
_DEFAULT_DB = Path(
    os.environ.get("DISK_CACHE_DB", "/app/workspace/memory/embed_cache.db")
)

_EMBED_SOFT_CAP = int(os.environ.get("DISK_CACHE_EMBED_CAP", "100000"))
_DECOMP_SOFT_CAP = int(os.environ.get("DISK_CACHE_DECOMP_CAP", "10000"))

_conn: Optional[sqlite3.Connection] = None
_conn_lock = threading.Lock()


def _get_conn() -> Optional[sqlite3.Connection]:
    """Lazy sqlite connection. Returns None if the file can't be opened."""
    global _conn
    if _conn is not None:
        return _conn
    with _conn_lock:
        if _conn is not None:
            return _conn
        try:
            _DEFAULT_DB.parent.mkdir(parents=True, exist_ok=True)
            c = sqlite3.connect(
                _DEFAULT_DB,
                check_same_thread=False,
                isolation_level=None,  # autocommit
                timeout=5.0,
            )
            c.execute("PRAGMA journal_mode = WAL")
            c.execute("PRAGMA synchronous = NORMAL")
            c.execute(
                "CREATE TABLE IF NOT EXISTS embed_cache ("
                "sha256 TEXT PRIMARY KEY, dim INT, vec BLOB, created_at REAL)"
            )
            c.execute(
                "CREATE TABLE IF NOT EXISTS decomp_cache ("
                "query_sha256 TEXT PRIMARY KEY, subqueries_json TEXT, created_at REAL)"
            )
            _conn = c
            return _conn
        except Exception as exc:
            logger.warning(f"disk_cache: unable to open {_DEFAULT_DB}: {exc}")
            return None


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


# ── Embedding cache ────────────────────────────────────────────────────────

def embed_get(text: str) -> Optional[list[float]]:
    """Look up an embedding by text hash. Returns None on miss."""
    c = _get_conn()
    if c is None:
        return None
    try:
        row = c.execute(
            "SELECT vec FROM embed_cache WHERE sha256 = ?", (_hash(text),)
        ).fetchone()
        if row is None:
            return None
        return list(array.array("f", row[0]))
    except Exception:
        return None


def embed_put(text: str, vec: list[float]) -> None:
    """Insert (or replace) an embedding. Best-effort — never raises."""
    c = _get_conn()
    if c is None or not vec:
        return
    try:
        packed = array.array("f", vec).tobytes()
        c.execute(
            "INSERT OR REPLACE INTO embed_cache(sha256, dim, vec, created_at) "
            "VALUES (?, ?, ?, ?)",
            (_hash(text), len(vec), packed, time.time()),
        )
    except Exception as exc:
        logger.debug(f"disk_cache.embed_put failed: {exc}")


# ── Decomposition cache ─────────────────────────────────────────────────────

def decomp_get(query: str) -> Optional[list[str]]:
    c = _get_conn()
    if c is None:
        return None
    try:
        row = c.execute(
            "SELECT subqueries_json FROM decomp_cache WHERE query_sha256 = ?",
            (_hash(query),),
        ).fetchone()
        if row is None:
            return None
        parsed = json.loads(row[0])
        if isinstance(parsed, list) and all(isinstance(s, str) for s in parsed):
            return parsed
        return None
    except Exception:
        return None


def decomp_put(query: str, subqueries: list[str]) -> None:
    c = _get_conn()
    if c is None or not subqueries:
        return
    try:
        c.execute(
            "INSERT OR REPLACE INTO decomp_cache(query_sha256, subqueries_json, created_at) "
            "VALUES (?, ?, ?)",
            (_hash(query), json.dumps(list(subqueries)), time.time()),
        )
    except Exception as exc:
        logger.debug(f"disk_cache.decomp_put failed: {exc}")


# ── Maintenance ─────────────────────────────────────────────────────────────

def prune() -> None:
    """Enforce soft caps by dropping oldest rows. Call periodically (idle job)."""
    c = _get_conn()
    if c is None:
        return
    try:
        for table, cap in (("embed_cache", _EMBED_SOFT_CAP), ("decomp_cache", _DECOMP_SOFT_CAP)):
            n = c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if n > cap:
                over = n - cap
                c.execute(
                    f"DELETE FROM {table} WHERE rowid IN ("
                    f"  SELECT rowid FROM {table} ORDER BY created_at ASC LIMIT ?"
                    f")",
                    (over,),
                )
                logger.info(f"disk_cache.prune: trimmed {over} from {table} (was {n})")
        c.execute("VACUUM")
    except Exception as exc:
        logger.debug(f"disk_cache.prune failed: {exc}")


def stats() -> dict:
    """Return current row counts and on-disk size. Useful for debugging."""
    c = _get_conn()
    out: dict = {"path": str(_DEFAULT_DB), "opened": c is not None}
    if c is None:
        return out
    try:
        out["embed_rows"] = c.execute("SELECT COUNT(*) FROM embed_cache").fetchone()[0]
        out["decomp_rows"] = c.execute("SELECT COUNT(*) FROM decomp_cache").fetchone()[0]
        if _DEFAULT_DB.exists():
            out["size_bytes"] = _DEFAULT_DB.stat().st_size
    except Exception:
        pass
    return out
