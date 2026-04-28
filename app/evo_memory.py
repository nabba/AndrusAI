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
    stored = False
    try:
        store(EVO_SUCCESSES, text, metadata)
        stored = True
    except Exception as e:
        logger.debug(f"evo_memory: failed to store success: {e}")
    finally:
        if stored:
            _queue_transfer_event(
                kind_name="evo_success",
                source_id=_evo_source_id("success", hypothesis, change_type),
                summary=f"[SUCCESS] {hypothesis[:160]}",
                payload={
                    "hypothesis": hypothesis,
                    "change_type": change_type,
                    "delta": round(delta, 6),
                    "files": list(files)[:8],
                    "detail": detail[:500],
                },
            )


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
    stored = False
    try:
        store(EVO_FAILURES, text, metadata)
        stored = True
    except Exception as e:
        logger.debug(f"evo_memory: failed to store failure: {e}")
    finally:
        if stored:
            _queue_transfer_event(
                kind_name="evo_failure",
                source_id=_evo_source_id("failure", hypothesis, change_type),
                summary=f"[FAILURE] {hypothesis[:160]}",
                payload={
                    "hypothesis": hypothesis,
                    "change_type": change_type,
                    "reason": reason[:500],
                },
            )


def _evo_source_id(kind: str, hypothesis: str, change_type: str) -> str:
    """Stable id from (kind, change_type, hypothesis prefix) — collisions
    within the same idle window are intentional (queue dedups by id)."""
    import hashlib
    h = hashlib.sha256(
        f"{kind}::{change_type}::{hypothesis[:200]}".encode()
    ).hexdigest()[:16]
    return f"evo_{kind}_{h}"


def _queue_transfer_event(
    *,
    kind_name: str,
    source_id: str,
    summary: str,
    payload: dict,
) -> None:
    """Append a transfer-memory event for nightly compilation.

    Failures swallowed — evolution storage must never break because the
    transfer-memory subsystem is unavailable.
    """
    try:
        from app.transfer_memory import append_event, TransferKind
        kind = TransferKind(kind_name)
        append_event(
            kind=kind,
            source_id=source_id,
            summary=summary,
            payload=payload,
        )
    except Exception:
        logger.debug("evo_memory: transfer_memory hook failed", exc_info=True)


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
