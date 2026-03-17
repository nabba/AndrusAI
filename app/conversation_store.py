"""
Persistent conversation history using SQLite.

Each Signal sender gets a rolling window of their recent exchanges stored
in /app/workspace/conversations.db.  This file is included in workspace
git backups so history survives power outages and redeployments.

Sender phone numbers are stored as a truncated HMAC-SHA256 so the raw
number is never written to disk.
"""
import hashlib
import hmac
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_gateway_secret

logger = logging.getLogger(__name__)

DB_PATH = Path("/app/workspace/conversations.db")

# Global connection pool (one connection per thread via threading.local)
_local = threading.local()
_init_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    """Return a thread-local SQLite connection, creating it if needed."""
    if not hasattr(_local, "conn") or _local.conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")   # concurrent reads during writes
        conn.execute("PRAGMA synchronous=NORMAL") # durable without full fsync overhead
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id TEXT    NOT NULL,
                role      TEXT    NOT NULL,  -- 'user' or 'assistant'
                content   TEXT    NOT NULL,
                ts        TEXT    NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sender_ts "
            "ON messages(sender_id, ts)"
        )
        conn.commit()
        _local.conn = conn
    return _local.conn


def _sender_id(sender: str) -> str:
    """Return a stable, non-reversible 16-char token for a sender number."""
    # Use gateway secret as HMAC key so IDs are unpredictable even if DB leaks
    try:
        key = get_gateway_secret().encode()
    except Exception:
        key = b"fallback"
    return hmac.new(key, sender.encode(), hashlib.sha256).hexdigest()[:16]


def add_message(sender: str, role: str, content: str) -> None:
    """Append a message (role='user' or 'assistant') to the conversation log."""
    ts = datetime.now(timezone.utc).isoformat()
    try:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO messages (sender_id, role, content, ts) VALUES (?, ?, ?, ?)",
            (_sender_id(sender), role, content, ts),
        )
        conn.commit()
    except Exception:
        logger.exception("conversation_store: failed to persist message")


def get_history(sender: str, n: int = 10) -> str:
    """
    Return the last *n* user+assistant exchanges as a formatted string,
    oldest-first, suitable for injecting into an LLM prompt.
    Returns "" if no history exists.
    """
    try:
        conn = _get_conn()
        rows = conn.execute(
            """
            SELECT role, content FROM (
                SELECT role, content, ts
                FROM messages
                WHERE sender_id = ?
                ORDER BY ts DESC
                LIMIT ?
            ) ORDER BY ts ASC
            """,
            (_sender_id(sender), n * 2),  # n exchanges = up to 2n rows
        ).fetchall()
    except Exception:
        logger.exception("conversation_store: failed to retrieve history")
        return ""

    if not rows:
        return ""

    lines = []
    for role, content in rows:
        label = "User" if role == "user" else "Assistant"
        # Truncate very long individual messages to keep prompt size bounded
        snippet = content[:600] + ("…" if len(content) > 600 else "")
        lines.append(f"{label}: {snippet}")
    return "\n".join(lines)
