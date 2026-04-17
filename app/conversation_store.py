"""
Persistent conversation history using SQLite.

Each Signal sender gets a rolling window of their recent exchanges stored
in /app/workspace/conversations.db.  This file is included in workspace
git backups so history survives power outages and redeployments.

Sender phone numbers are stored as a truncated HMAC-SHA256 so the raw
number is never written to disk.

Task tracking: the tasks table records timing and success/failure for
each user request, providing data for the metrics system.
"""
import hashlib
import hmac
import logging
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

from app.config import get_gateway_secret

logger = logging.getLogger(__name__)

DB_PATH = Path("/app/workspace/conversations.db")

# Global connection pool (one connection per thread via threading.local)
_local = threading.local()
_init_lock = threading.Lock()


# ── Schema migrations (T3-10) ────────────────────────────────────────────────

_MIGRATIONS: list[tuple[str, str]] = [
    ("v1_messages", """
        CREATE TABLE IF NOT EXISTS messages (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id TEXT    NOT NULL,
            role      TEXT    NOT NULL,
            content   TEXT    NOT NULL,
            ts        TEXT    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sender_ts ON messages(sender_id, ts);
    """),
    ("v2_tasks", """
        CREATE TABLE IF NOT EXISTS tasks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id   TEXT    NOT NULL,
            crew        TEXT    NOT NULL DEFAULT '',
            started_at  TEXT    NOT NULL,
            completed_at TEXT,
            success     INTEGER NOT NULL DEFAULT 1,
            duration_s  REAL,
            error_type  TEXT    DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_started ON tasks(started_at);
    """),
    ("v3_fts5", """
        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
        USING fts5(content, sender_id UNINDEXED, role UNINDEXED, ts UNINDEXED,
                   content='messages', content_rowid='id');
    """),
    ("v3_fts5_triggers", """
        CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, content, sender_id, role, ts)
            VALUES (new.id, new.content, new.sender_id, new.role, new.ts);
        END;
        CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, content, sender_id, role, ts)
            VALUES ('delete', old.id, old.content, old.sender_id, old.role, old.ts);
        END;
    """),
]


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Run pending schema migrations. Idempotent."""
    conn.execute("CREATE TABLE IF NOT EXISTS _schema_version (name TEXT PRIMARY KEY, applied_at TEXT)")
    applied = {r[0] for r in conn.execute("SELECT name FROM _schema_version").fetchall()}
    for name, sql in _MIGRATIONS:
        if name not in applied:
            try:
                conn.executescript(sql)
                conn.execute(
                    "INSERT INTO _schema_version VALUES (?, ?)",
                    (name, datetime.now(timezone.utc).isoformat()),
                )
                logger.info(f"conversation_store: applied migration '{name}'")
            except sqlite3.OperationalError as exc:
                # FTS5 may not be compiled in on some SQLite builds — log and skip
                logger.warning(f"conversation_store: migration '{name}' skipped: {exc}")
    conn.commit()


def _get_conn() -> sqlite3.Connection:
    """Return a thread-local SQLite connection, creating it if needed."""
    if not hasattr(_local, "conn") or _local.conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _run_migrations(conn)
        _local.conn = conn
    return _local.conn


_SENDER_KEY_FILE = Path("/app/workspace/.sender_key")


def _get_stable_sender_key() -> bytes:
    """Get a stable HMAC key for sender ID hashing.

    Priority: gateway_secret > persisted file > generate and persist new.
    The persisted file ensures sender IDs survive process restarts.
    """
    # Priority 1: gateway secret (best — derived from env config)
    try:
        secret = get_gateway_secret()
        if secret and len(secret) >= 8:
            return secret.encode()
    except Exception:
        pass
    # Priority 2: persisted key file (survives restarts)
    try:
        if _SENDER_KEY_FILE.exists():
            key = _SENDER_KEY_FILE.read_bytes()
            if len(key) >= 16:
                return key
    except Exception:
        pass
    # Priority 3: generate and persist new key
    import secrets
    key = secrets.token_bytes(32)
    try:
        _SENDER_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SENDER_KEY_FILE.write_bytes(key)
        logger.info("conversation_store: generated persistent sender key")
    except Exception:
        logger.warning("conversation_store: could not persist sender key — IDs may change on restart")
    return key


def _sender_id(sender: str) -> str:
    """Return a stable, non-reversible 16-char token for a sender number."""
    key = _get_stable_sender_key()
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

    # Filter out system/internal responses that could contaminate crew context
    _INTERNAL_PREFIXES = (
        "LLM Discovery:", "Evolution session", "Retrospective analysis",
        "Self-heal:", "Improvement scan", "Tech Radar", "Code audit",
        "Training pipeline", "Consciousness probe", "Behavioral assessment",
        "Prosocial session", "Fiction ingest", "Knowledge base ingestion",
    )

    lines = []
    for role, content in rows:
        label = "User" if role == "user" else "Assistant"
        # Skip internal system responses from conversation history
        if role == "assistant" and any(content.strip().startswith(p) for p in _INTERNAL_PREFIXES):
            continue
        # Truncate assistant responses to prevent context pollution
        # but keep enough for follow-up context (was 300, raised to 500)
        max_len = 500 if role == "assistant" else 600
        snippet = content[:max_len] + ("…" if len(content) > max_len else "")
        lines.append(f"{label}: {snippet}")
    return "\n".join(lines)


def get_last_assistant_message(sender: str) -> str:
    """Return the raw content of the most recent assistant reply to this
    sender, or empty string. Used by Phase 15 grounding to supply
    prior-response context to correction detection."""
    try:
        conn = _get_conn()
        row = conn.execute(
            """
            SELECT content FROM messages
            WHERE sender_id = ? AND role = 'assistant'
            ORDER BY ts DESC
            LIMIT 1
            """,
            (_sender_id(sender),),
        ).fetchone()
    except Exception:
        logger.debug("conversation_store: get_last_assistant_message failed",
                     exc_info=True)
        return ""
    return str(row[0]) if row else ""


# ── Task tracking (for metrics) ─────────────────────────────────────────────

def start_task(sender: str, crew: str = "") -> int:
    """Record the start of a task. Returns the task row ID."""
    ts = datetime.now(timezone.utc).isoformat()
    try:
        conn = _get_conn()
        cur = conn.execute(
            "INSERT INTO tasks (sender_id, crew, started_at) VALUES (?, ?, ?)",
            (_sender_id(sender), crew, ts),
        )
        conn.commit()
        return cur.lastrowid
    except Exception:
        logger.exception("conversation_store: failed to start task")
        return -1


def complete_task(task_id: int, success: bool = True, error_type: str = "") -> None:
    """Record the completion of a task with timing."""
    ts = datetime.now(timezone.utc).isoformat()
    try:
        conn = _get_conn()
        # Compute duration from started_at
        row = conn.execute(
            "SELECT started_at FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        duration = 0.0
        if row:
            try:
                started = datetime.fromisoformat(row[0])
                duration = (datetime.now(timezone.utc) - started).total_seconds()
            except (ValueError, TypeError):
                pass
        conn.execute(
            "UPDATE tasks SET completed_at = ?, success = ?, duration_s = ?, error_type = ? WHERE id = ?",
            (ts, 1 if success else 0, duration, error_type, task_id),
        )
        conn.commit()
    except Exception:
        logger.exception("conversation_store: failed to complete task")


def update_task_crew(task_id: int, crew: str) -> None:
    """Update the crew name on a task (set after Commander routing)."""
    try:
        conn = _get_conn()
        conn.execute("UPDATE tasks SET crew = ? WHERE id = ?", (crew, task_id))
        conn.commit()
    except Exception:
        logger.debug("conversation_store: failed to update task crew", exc_info=True)


def count_recent_tasks(hours: int = 24) -> tuple[int, int]:
    """Count (total, successful) tasks in the last N hours."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    try:
        conn = _get_conn()
        total = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE started_at > ? AND completed_at IS NOT NULL",
            (cutoff,),
        ).fetchone()[0]
        successful = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE started_at > ? AND completed_at IS NOT NULL AND success = 1",
            (cutoff,),
        ).fetchone()[0]
        return (total, successful)
    except Exception:
        logger.exception("conversation_store: failed to count tasks")
        return (0, 0)


def avg_response_time(hours: int = 24) -> float:
    """Average task duration in seconds over the last N hours."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT AVG(duration_s) FROM tasks WHERE started_at > ? AND completed_at IS NOT NULL AND duration_s > 0",
            (cutoff,),
        ).fetchone()
        return row[0] if row and row[0] else 0.0
    except Exception:
        logger.exception("conversation_store: failed to compute avg response time")
        return 0.0


# Default ETAs (seconds) — used when no historical data exists yet
_DEFAULT_ETA: dict[str, int] = {
    "commander": 30,
    "research": 120,
    "coding": 180,
    "writing": 90,
    "self_improvement": 300,
    "retrospective": 180,
}


def get_crew_avg_duration(crew: str) -> float:
    """Average task duration for a specific crew (last 7 days, successful tasks).

    Returns seconds. Falls back to defaults if no historical data.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT AVG(duration_s), COUNT(*) FROM tasks "
            "WHERE crew = ? AND started_at > ? AND completed_at IS NOT NULL "
            "AND success = 1 AND duration_s > 0",
            (crew, cutoff),
        ).fetchone()
        avg, count = row if row else (None, 0)
        if avg and count >= 3:
            return round(avg, 1)
    except Exception:
        logger.debug("conversation_store: failed to get crew avg duration", exc_info=True)
    return float(_DEFAULT_ETA.get(crew, 120))


def estimate_eta(crew: str) -> int:
    """Return estimated seconds for a task on this crew, based on history."""
    return int(get_crew_avg_duration(crew))


# ── FTS5 full-text search (T3-10) ────────────────────────────────────────────

def search_messages(query: str, sender: str | None = None, limit: int = 10) -> list[dict]:
    """Full-text search across conversations using FTS5.

    Returns a list of dicts: {role, content_snippet, ts}. Empty list on no
    results or if FTS5 is unavailable on this SQLite build.
    """
    if not query or not query.strip():
        return []
    import re
    clean = re.sub(r'[^\w\s]', ' ', query).strip()
    if not clean:
        return []
    try:
        conn = _get_conn()
        if sender:
            sid = _sender_id(sender)
            rows = conn.execute(
                """SELECT m.role, m.content, m.ts,
                          snippet(messages_fts, 0, '>>>', '<<<', '...', 40)
                   FROM messages_fts
                   JOIN messages m ON m.id = messages_fts.rowid
                   WHERE messages_fts MATCH ? AND m.sender_id = ?
                   ORDER BY m.ts DESC LIMIT ?""",
                (clean, sid, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT m.role, m.content, m.ts,
                          snippet(messages_fts, 0, '>>>', '<<<', '...', 40)
                   FROM messages_fts
                   JOIN messages m ON m.id = messages_fts.rowid
                   WHERE messages_fts MATCH ?
                   ORDER BY m.ts DESC LIMIT ?""",
                (clean, limit),
            ).fetchall()
        return [{"role": r[0], "content_snippet": r[3] or r[1][:200], "ts": r[2]} for r in rows]
    except Exception:
        logger.debug("search_messages failed", exc_info=True)
        return []


def rebuild_fts_index() -> int:
    """Rebuild FTS5 index from existing data. Idempotent."""
    try:
        conn = _get_conn()
        conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        logger.info(f"FTS5 rebuilt: {count} messages")
        return count
    except Exception:
        logger.debug("FTS5 rebuild failed", exc_info=True)
        return 0
