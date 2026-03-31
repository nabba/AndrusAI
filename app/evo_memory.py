"""
evo_memory.py — Evolutionary memory for the AVO self-evolution pipeline.

Stores successful and failed mutation patterns in ChromaDB for retrieval
during the planning phase. Enables learning from past experiments and
cross-session transfer of evolutionary knowledge.

Uses existing chromadb_manager infrastructure — no new databases.
"""

import logging
from datetime import datetime, timezone

from app.memory.chromadb_manager import store, retrieve_with_metadata

logger = logging.getLogger(__name__)

EVO_SUCCESSES = "evo_successes"
EVO_FAILURES = "evo_failures"


def store_success(
    hypothesis: str,
    change_type: str,
    delta: float,
    files: list[str],
    detail: str,
) -> None:
    """Record a successful mutation pattern for future reference."""
    text = f"[SUCCESS] {hypothesis}\nResult: {detail}\nFiles: {', '.join(files)}"
    metadata = {
        "change_type": change_type,
        "delta": round(delta, 6),
        "files": ",".join(files)[:500],
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    try:
        store(EVO_SUCCESSES, text, metadata)
    except Exception as e:
        logger.debug(f"evo_memory: failed to store success: {e}")


def store_failure(
    hypothesis: str,
    change_type: str,
    reason: str,
) -> None:
    """Record a failed mutation pattern to avoid repeating it."""
    text = f"[FAILURE] {hypothesis}\nReason: {reason}"
    metadata = {
        "change_type": change_type,
        "reason": reason[:500],
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    try:
        store(EVO_FAILURES, text, metadata)
    except Exception as e:
        logger.debug(f"evo_memory: failed to store failure: {e}")


def recall_similar_successes(query: str, n: int = 5) -> list[dict]:
    """Find past successes similar to the given query/hypothesis."""
    try:
        return retrieve_with_metadata(EVO_SUCCESSES, query, n)
    except Exception:
        return []


def recall_similar_failures(query: str, n: int = 5) -> list[dict]:
    """Find past failures similar to the given query/hypothesis."""
    try:
        return retrieve_with_metadata(EVO_FAILURES, query, n)
    except Exception:
        return []


def format_memory_context(hypothesis: str) -> str:
    """Build a formatted context string of relevant evolutionary memory.

    Returns a concise summary of similar past successes and failures
    for injection into the AVO planning prompt.
    """
    if not hypothesis:
        return ""

    parts = []

    successes = recall_similar_successes(hypothesis, n=3)
    if successes:
        lines = []
        for s in successes:
            doc = s.get("document", "")
            delta = s.get("metadata", {}).get("delta", "?")
            lines.append(f"  - {doc[:120]} (delta={delta})")
        parts.append("PAST SUCCESSES (similar experiments that worked):\n" + "\n".join(lines))

    failures = recall_similar_failures(hypothesis, n=3)
    if failures:
        lines = []
        for f in failures:
            doc = f.get("document", "")
            reason = f.get("metadata", {}).get("reason", "")[:80]
            lines.append(f"  - {doc[:120]}")
        parts.append("PAST FAILURES (similar experiments that failed — do NOT repeat):\n" + "\n".join(lines))

    return "\n\n".join(parts)
