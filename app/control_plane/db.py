"""Database connection pool for the control_plane schema.

Reuses the existing Mem0 PostgreSQL instance. Thread-safe singleton pool.
"""
import logging
import threading

import psycopg2
from psycopg2 import pool as pg_pool

logger = logging.getLogger(__name__)

_pool: pg_pool.ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()

def get_pool() -> pg_pool.ThreadedConnectionPool | None:
    """Get or create the connection pool. Thread-safe singleton."""
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is not None:
            return _pool
        try:
            from app.config import get_settings
            s = get_settings()
            if not s.mem0_postgres_url:
                logger.warning("control_plane: no postgres URL configured")
                return None
            # Pool sizing notes (2026-04-24):
            # The original maxconn=4 was sized for a serial commander
            # → crew → tool flow.  The research_orchestrator fires up
            # to ``max_subjects_in_parallel`` worker threads, and each
            # worker's tool calls trigger span_events writes (start_span
            # + complete_span) + memory/mem0/mem0-team writes + budget
            # updates + ticket updates — easily 8–15 concurrent conn
            # requests during a 30-subject enrichment task.  We saw the
            # PSP tender task (76 & 77) exhaust maxconn=4 and crash the
            # gateway TWICE in 5 minutes, each time killing the user's
            # research mid-delivery.  Bumping to 24 gives headroom for
            # 3× orchestrator fan-out depths simultaneously; well under
            # Postgres's default ``max_connections=100`` ceiling.
            import os as _os
            _maxconn = int(_os.environ.get("CONTROL_PLANE_POOL_MAX", "24"))
            _pool = pg_pool.ThreadedConnectionPool(
                minconn=2, maxconn=_maxconn,
                dsn=s.mem0_postgres_url,
            )
            logger.info(
                "control_plane: connection pool created "
                "(minconn=2, maxconn=%d)", _maxconn,
            )
            return _pool
        except Exception as e:
            logger.warning(f"control_plane: pool creation failed: {e}")
            return None

def _reset_pool() -> None:
    """Destroy and recreate the pool on persistent connection failures."""
    global _pool
    with _pool_lock:
        if _pool:
            try:
                _pool.closeall()
            except Exception:
                pass
            _pool = None

def execute(query: str, params: tuple = (), fetch: bool = False) -> list | None:
    """Execute a query using the pool. Returns rows if fetch=True.

    Validates connection health before use. Resets pool on persistent failures.
    """
    p = get_pool()
    if not p:
        return None
    conn = None
    try:
        conn = p.getconn()
        # Validate connection is alive before use
        try:
            conn.autocommit = True
        except (psycopg2.InterfaceError, psycopg2.OperationalError):
            # Stale connection — close and get fresh one
            try:
                p.putconn(conn, close=True)
            except Exception:
                pass
            conn = p.getconn()
            conn.autocommit = True

        with conn.cursor() as cur:
            cur.execute(query, params)
            if fetch:
                cols = [d[0] for d in cur.description] if cur.description else []
                return [dict(zip(cols, row)) for row in cur.fetchall()]
            return []
    except (psycopg2.InterfaceError, psycopg2.OperationalError) as e:
        logger.warning(f"control_plane: connection error, resetting pool: {e}")
        _reset_pool()
        return None
    except Exception as e:
        logger.error(f"control_plane SQL error: {e}")
        return None
    finally:
        if conn:
            try:
                p.putconn(conn)
            except Exception:
                pass

def execute_one(query: str, params: tuple = ()) -> dict | None:
    """Execute and return a single row."""
    rows = execute(query, params, fetch=True)
    return rows[0] if rows else None

def execute_scalar(query: str, params: tuple = ()):
    """Execute and return a single value."""
    p = get_pool()
    if not p:
        return None
    conn = None
    try:
        conn = p.getconn()
        try:
            conn.autocommit = True
        except (psycopg2.InterfaceError, psycopg2.OperationalError):
            try:
                p.putconn(conn, close=True)
            except Exception:
                pass
            conn = p.getconn()
            conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(query, params)
            row = cur.fetchone()
            return row[0] if row else None
    except (psycopg2.InterfaceError, psycopg2.OperationalError) as e:
        logger.warning(f"control_plane: connection error in scalar, resetting pool: {e}")
        _reset_pool()
        return None
    except Exception as e:
        logger.error(f"control_plane SQL error: {e}")
        return None
    finally:
        if conn:
            try:
                p.putconn(conn)
            except Exception:
                pass
