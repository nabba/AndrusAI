"""
llm_benchmarks.py — Track LLM model performance per task type.

Stores outcomes (success/failure, latency, tokens) in SQLite.
Used by llm_selector to prefer models that historically perform well.
"""

import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path("/app/workspace/llm_benchmarks.db")
_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS benchmarks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                model       TEXT NOT NULL,
                task_type   TEXT NOT NULL,
                success     INTEGER NOT NULL,  -- 1=success, 0=failure
                latency_ms  INTEGER,
                tokens      INTEGER,
                ts          TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_model_task "
            "ON benchmarks(model, task_type)"
        )
        conn.commit()
        _local.conn = conn
    return _local.conn


def record(
    model: str,
    task_type: str,
    success: bool,
    latency_ms: int = 0,
    tokens: int = 0,
) -> None:
    """Record a model invocation outcome."""
    try:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO benchmarks (model, task_type, success, latency_ms, tokens, ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (model, task_type, int(success), latency_ms, tokens,
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    except Exception:
        logger.debug("llm_benchmarks: failed to record", exc_info=True)


def get_scores(task_type: str) -> dict[str, float]:
    """
    Return model→score for a task type.
    Score = success_rate * speed_factor (higher is better).
    Only considers last 50 runs per model.
    """
    try:
        conn = _get_conn()
        rows = conn.execute(
            """
            SELECT model,
                   AVG(success) as success_rate,
                   AVG(latency_ms) as avg_latency,
                   COUNT(*) as runs
            FROM (
                SELECT model, success, latency_ms
                FROM benchmarks
                WHERE task_type = ?
                ORDER BY ts DESC
                LIMIT 200
            )
            GROUP BY model
            HAVING runs >= 2
            """,
            (task_type,),
        ).fetchall()
    except Exception:
        return {}

    scores = {}
    for model, success_rate, avg_latency, runs in rows:
        # Speed factor: penalize slow models (normalize to 0.5-1.0 range)
        speed = max(0.5, 1.0 - (avg_latency or 0) / 120000)  # 120s → 0.5
        # Confidence: more runs → more weight (caps at 1.0 after 10 runs)
        confidence = min(1.0, runs / 10)
        scores[model] = (success_rate or 0) * speed * (0.5 + 0.5 * confidence)
    return scores


def get_summary(n: int = 10) -> str:
    """Format benchmark summary for display."""
    try:
        conn = _get_conn()
        rows = conn.execute(
            """
            SELECT model, task_type,
                   ROUND(AVG(success) * 100, 0) as pct,
                   ROUND(AVG(latency_ms) / 1000.0, 1) as avg_sec,
                   COUNT(*) as runs
            FROM benchmarks
            GROUP BY model, task_type
            ORDER BY runs DESC
            LIMIT ?
            """,
            (n,),
        ).fetchall()
    except Exception:
        return "No benchmark data yet."

    if not rows:
        return "No benchmark data yet."

    lines = ["Model Benchmarks:\n"]
    for model, task, pct, avg_sec, runs in rows:
        lines.append(f"  {model} [{task}]: {pct:.0f}% success, {avg_sec}s avg, {runs} runs")
    return "\n".join(lines)
