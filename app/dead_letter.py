"""
dead_letter.py — Dead letter queue for failed user messages.

When handle_task() fails, the message is enqueued here for a single retry
after a 5-minute delay (giving self-heal time to fix the root cause).

Persistence: dbm.sqlite3 (Python 3.13+, stdlib, lightweight).
Safety: max 1 retry, max 50 entries, 2000 char text limit.

DGM Safety: DLQ retry uses Commander.handle() directly — does NOT re-enter
handle_task() to avoid infinite re-enqueue loops.
"""

import hashlib
import json
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

_DLQ_PATH = "/app/workspace/memory/dead_letter_queue"
_lock = threading.Lock()
MAX_RETRIES = 1
MIN_RETRY_DELAY_S = 300  # 5 minutes
MAX_DLQ_SIZE = 50
MAX_TEXT_LEN = 2000


def enqueue(sender: str, text: str, error_type: str, trace_id: str = "") -> None:
    """Enqueue a failed message for later retry."""
    try:
        import dbm.sqlite3
        os.makedirs(os.path.dirname(_DLQ_PATH), exist_ok=True)

        text_hash = hashlib.md5(f"{sender}:{text[:100]}".encode()).hexdigest()[:8]
        key = f"dlq:{int(time.time())}:{text_hash}"

        entry = {
            "sender": sender,
            "text": text[:MAX_TEXT_LEN],
            "error_type": error_type,
            "trace_id": trace_id,
            "enqueued_at": time.time(),
            "retry_count": 0,
            "failed": False,
        }

        with _lock:
            with dbm.sqlite3.open(_DLQ_PATH, "c") as db:
                db[key] = json.dumps(entry)
                # Prune oldest entries if over limit
                all_keys = sorted(db.keys())
                while len(all_keys) > MAX_DLQ_SIZE:
                    oldest = all_keys.pop(0)
                    del db[oldest]

        logger.info(f"dead_letter: enqueued message from {sender[:4]}... (error={error_type})")
    except Exception:
        logger.debug("dead_letter: enqueue failed", exc_info=True)


def dequeue_retryable() -> list[dict]:
    """Get entries eligible for retry (under retry limit + past delay period)."""
    try:
        import dbm.sqlite3
        now = time.time()
        retryable = []

        with _lock:
            with dbm.sqlite3.open(_DLQ_PATH, "c") as db:
                for key in list(db.keys()):
                    try:
                        entry = json.loads(db[key])
                        if entry.get("failed"):
                            continue
                        if entry.get("retry_count", 0) >= MAX_RETRIES:
                            continue
                        delay = MIN_RETRY_DELAY_S * (2 ** entry.get("retry_count", 0))
                        if now - entry.get("enqueued_at", 0) < delay:
                            continue
                        # Mark as in-flight (increment retry count)
                        entry["retry_count"] = entry.get("retry_count", 0) + 1
                        db[key] = json.dumps(entry)
                        entry["_key"] = key.decode() if isinstance(key, bytes) else key
                        retryable.append(entry)
                    except Exception:
                        pass
        return retryable
    except Exception:
        return []


def mark_success(key: str) -> None:
    """Remove successfully retried entry."""
    try:
        import dbm.sqlite3
        with _lock:
            with dbm.sqlite3.open(_DLQ_PATH, "c") as db:
                if key.encode() in db or key in db:
                    del db[key]
        logger.info(f"dead_letter: retry succeeded, removed {key}")
    except Exception:
        pass


def mark_permanent_failure(key: str) -> None:
    """Mark entry as permanently failed (kept for audit)."""
    try:
        import dbm.sqlite3
        with _lock:
            with dbm.sqlite3.open(_DLQ_PATH, "c") as db:
                k = key.encode() if isinstance(key, str) else key
                if k in db:
                    entry = json.loads(db[k])
                    entry["failed"] = True
                    entry["failed_at"] = time.time()
                    db[k] = json.dumps(entry)
        logger.info(f"dead_letter: permanently failed {key}")
    except Exception:
        pass


def get_stats() -> dict:
    """Get DLQ statistics for dashboard."""
    try:
        import dbm.sqlite3
        stats = {"pending": 0, "failed": 0, "total": 0}
        with _lock:
            with dbm.sqlite3.open(_DLQ_PATH, "c") as db:
                for key in db.keys():
                    try:
                        entry = json.loads(db[key])
                        stats["total"] += 1
                        if entry.get("failed"):
                            stats["failed"] += 1
                        elif entry.get("retry_count", 0) < MAX_RETRIES:
                            stats["pending"] += 1
                    except Exception:
                        pass
        return stats
    except Exception:
        return {"pending": 0, "failed": 0, "total": 0}
