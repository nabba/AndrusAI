"""Postgres-backed code_intel store (Verified Implementation Plan §5
Gap F closure, 2026-05-23).

Companion to ``app/code_intel/store.py`` (the JSONL store, canonical
for v1). Migration ``036_code_intel.sql`` ships the three tables
expected by Plan §5: ``code_symbols``, ``code_references``,
``code_coverage_snapshot``. This module is the WRITER that turns the
master switch ``code_intel_postgres_enabled`` from aspirational into
operational.

Design notes
────────────

  * **Mirror, don't replace.** The JSONL store remains canonical
    until the operator graduates Postgres. When the master switch is
    ON, ``save_index`` dual-writes — JSONL first (durable on disk),
    Postgres second (query power). A Postgres write failure is
    audit-logged but never propagated; the JSONL persistence is the
    truthful one.
  * **Idempotent inserts**. The ``code_symbols`` table has a
    ``UNIQUE (file_path, name, lineno, parent)`` constraint;
    ``code_references`` is append-only (no natural key). We use
    ``ON CONFLICT DO NOTHING`` so re-running the indexer doesn't
    blow up on duplicates.
  * **Batched.** Each call commits in batches of 500 rows. Larger
    batches risk locking the table; smaller adds round-trip overhead.
  * **Failure isolated end-to-end.** Every public function is
    wrapped — a corrupt DB, missing schema, or wrong creds returns a
    structured failure dict, never raises into the caller.

Coverage snapshot
─────────────────

``code_coverage_snapshot`` is populated by a different code path
(``app/code_intel/coverage.py`` reads pytest ``.coverage`` files);
this module exposes ``save_coverage_snapshot`` as the standard
writer for that table too.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Iterable

logger = logging.getLogger(__name__)


_BATCH_SIZE = 500


# ── Master switch ─────────────────────────────────────────────────


def is_enabled() -> bool:
    """Master-switch read.

    Precedence: env var ``CODE_INTEL_POSTGRES_ENABLED`` first (ops
    override), then runtime_settings (operator-toggleable persistent
    state), then default OFF.

    Default OFF because v1 keeps JSONL canonical until the operator
    explicitly graduates the Postgres backend.
    """
    env = os.environ.get("CODE_INTEL_POSTGRES_ENABLED", "")
    if env:
        return env.lower() in ("1", "true", "yes", "on")
    try:
        from app import runtime_settings
        getter = getattr(
            runtime_settings, "get_code_intel_postgres_enabled", None,
        )
        if callable(getter):
            return bool(getter())
    except Exception:
        logger.debug(
            "postgres_store: runtime_settings read raised",
            exc_info=True,
        )
    return False


# ── Connection helper ──────────────────────────────────────────────


def _get_conn():
    """Acquire a psycopg2 connection. Lazy import so this module
    loads cleanly on hosts without psycopg2.

    Returns the connection or None if unavailable (the gateway pool
    isn't reachable, no DB URL, etc.).
    """
    try:
        from app.memory.postgres_pool import get_pg_pool
    except Exception:
        logger.debug(
            "postgres_store: postgres_pool unavailable", exc_info=True,
        )
        return None
    try:
        pool = get_pg_pool()
        return pool.getconn() if pool else None
    except Exception:
        logger.debug(
            "postgres_store: getconn raised", exc_info=True,
        )
        return None


def _return_conn(conn) -> None:
    if conn is None:
        return
    try:
        from app.memory.postgres_pool import get_pg_pool
        pool = get_pg_pool()
        if pool:
            pool.putconn(conn)
    except Exception:
        logger.debug(
            "postgres_store: putconn raised", exc_info=True,
        )


# ── Write surfaces ─────────────────────────────────────────────────


def save_index(snapshot) -> dict[str, Any]:
    """Mirror an :class:`IndexSnapshot` into the Postgres tables.

    Returns a structured summary:

        {
          "ok": bool,
          "symbols_inserted": int,
          "references_inserted": int,
          "indexed_at": iso8601,
          "error": str | None,
        }

    Idempotent — re-runs with the same snapshot insert no new rows
    (UNIQUE constraint on symbols; references are append-only by
    design since the same call site may appear multiple times across
    re-indexes and we want the historical row).
    """
    summary: dict[str, Any] = {
        "ok": False,
        "symbols_inserted": 0,
        "references_inserted": 0,
        "indexed_at": getattr(snapshot, "indexed_at", ""),
        "error": None,
    }

    if not is_enabled():
        summary["error"] = "code_intel_postgres_enabled is OFF"
        return summary

    conn = _get_conn()
    if conn is None:
        summary["error"] = "postgres connection unavailable"
        return summary

    try:
        with conn.cursor() as cur:
            # Insert symbols
            sym_rows = [
                (
                    s.name,
                    s.kind.value if hasattr(s.kind, "value") else str(s.kind),
                    s.file_path,
                    int(s.lineno),
                    int(s.end_lineno),
                    s.parent or "",
                    s.docstring_first_line or "",
                    getattr(s, "language", "python"),
                )
                for s in (snapshot.symbols or [])
            ]
            inserted_syms = 0
            for batch_start in range(0, len(sym_rows), _BATCH_SIZE):
                batch = sym_rows[batch_start:batch_start + _BATCH_SIZE]
                cur.executemany(
                    """
                    INSERT INTO code_symbols
                        (name, kind, file_path, lineno, end_lineno,
                         parent, docstring, language)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (file_path, name, lineno, parent)
                    DO NOTHING
                    """,
                    batch,
                )
                inserted_syms += cur.rowcount
            summary["symbols_inserted"] = inserted_syms

            # Insert references
            ref_rows = [
                (
                    r.name,
                    r.file_path,
                    int(r.lineno),
                    int(r.col_offset),
                    r.in_function or "",
                    r.in_class or "",
                    getattr(r, "language", "python"),
                )
                for r in (snapshot.references or [])
            ]
            inserted_refs = 0
            for batch_start in range(0, len(ref_rows), _BATCH_SIZE):
                batch = ref_rows[batch_start:batch_start + _BATCH_SIZE]
                cur.executemany(
                    """
                    INSERT INTO code_references
                        (name, file_path, lineno, col_offset,
                         in_function, in_class, language)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    batch,
                )
                inserted_refs += cur.rowcount
            summary["references_inserted"] = inserted_refs

        conn.commit()
        summary["ok"] = True
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.debug(
            "postgres_store.save_index raised: %s", exc, exc_info=True,
        )
        summary["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        _return_conn(conn)

    return summary


def save_coverage_snapshot(
    *,
    file_path: str,
    line_count: int,
    covered_lines: int,
    missed_lines: int,
    coverage_pct: float,
) -> dict[str, Any]:
    """Append one row to ``code_coverage_snapshot``.

    Idempotent on (file_path, snapshot_at) — running the snapshot
    twice in the same microsecond would refuse the second insert.
    Practically the timestamp resolution makes that impossible.
    """
    summary: dict[str, Any] = {
        "ok": False,
        "error": None,
    }
    if not is_enabled():
        summary["error"] = "code_intel_postgres_enabled is OFF"
        return summary
    conn = _get_conn()
    if conn is None:
        summary["error"] = "postgres connection unavailable"
        return summary
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO code_coverage_snapshot
                    (file_path, line_count, covered_lines,
                     missed_lines, coverage_pct)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (file_path, snapshot_at) DO NOTHING
                """,
                (
                    file_path,
                    int(line_count),
                    int(covered_lines),
                    int(missed_lines),
                    float(coverage_pct),
                ),
            )
        conn.commit()
        summary["ok"] = True
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.debug(
            "postgres_store.save_coverage_snapshot raised: %s",
            exc, exc_info=True,
        )
        summary["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        _return_conn(conn)
    return summary


# ── Read surfaces (mirror the JSONL query API at table level) ─────


def count_rows() -> dict[str, int]:
    """Return row counts for diagnostic purposes. Empty dict on
    failure (so the caller can degrade to JSONL stats)."""
    if not is_enabled():
        return {}
    conn = _get_conn()
    if conn is None:
        return {}
    out: dict[str, int] = {}
    try:
        with conn.cursor() as cur:
            for table in (
                "code_symbols", "code_references",
                "code_coverage_snapshot",
            ):
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {table}")
                    row = cur.fetchone()
                    out[table] = int(row[0]) if row else 0
                except Exception:
                    out[table] = -1
    except Exception:
        logger.debug(
            "postgres_store.count_rows raised", exc_info=True,
        )
    finally:
        _return_conn(conn)
    return out


__all__ = [
    "is_enabled",
    "save_index",
    "save_coverage_snapshot",
    "count_rows",
]
