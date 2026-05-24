"""recovery.auto_thread — auto-open Q8 threads on hard questions.

Tier 2.4 of the 2026-05-24 ultrathink analysis closure.

When the Commander + Recovery Loop both fail to answer a question
(every refusal-detection strategy exhausted, capability-librarian
empty, no candidate strategy succeeded), the system currently
returns a partial / "I don't know" reply and forgets the question.

A long-horizon Q8 thread is the right surface for these — the
operator's stated decision (PROGRAM §46.1) is that hard questions
deserve a tracked line of inquiry, not a forgotten answer.

This module wires that automatically: after every exhausted recovery
attempt, ``maybe_open_thread`` runs. Dedup against open threads with
similar titles prevents thread explosion.

Composes with — does not replace — the existing
``app/threads/approaches.py:consult_before_create`` (which fires a
ONE-SHOT Signal notification when matching past closures exist).
auto_thread is the COMPLEMENT: it creates the thread record when
the situation warrants persistence + future continuation.

Master switch: ``recovery_auto_thread_enabled`` (default OFF —
opt-in, because the thread surface is operator-visible and we don't
want to flood it).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── Tunables ─────────────────────────────────────────────────────────────

# Minimum question length to be worth threading. Trivial refusals
# (one-word "no") don't deserve a tracked inquiry.
_MIN_QUESTION_CHARS = 30

# Maximum auto-threads per 24h to prevent flooding.
_MAX_THREADS_PER_DAY = 3

# Similarity threshold for dedup against open threads. Token-overlap
# Jaccard >= this → considered duplicate.
_DEDUP_JACCARD_THRESHOLD = 0.5

# State file storing per-day thread emission count.
_STATE_KEY = "recovery_auto_thread"


def _enabled() -> bool:
    try:
        from app import runtime_settings

        return bool(runtime_settings.get_recovery_auto_thread_enabled())
    except Exception:
        return False


import re


_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text or "")}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    if union == 0:
        return 0.0
    return inter / union


def _list_open_threads() -> list[Any]:
    try:
        from app.threads.lifecycle import list_threads

        return list(list_threads(status="open"))
    except Exception:
        logger.debug("auto_thread: list_threads unavailable", exc_info=True)
        return []


def _has_similar_open_thread(question: str) -> bool:
    qt = _tokens(question)
    if not qt:
        return False
    for t in _list_open_threads():
        title = str(getattr(t, "title", "") or "")
        if _jaccard(qt, _tokens(title)) >= _DEDUP_JACCARD_THRESHOLD:
            return True
    return False


def _count_recent_auto_threads() -> int:
    """Count auto-threads opened in the last 24h. Used for rate limit."""
    try:
        from app.threads.lifecycle import list_threads
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        count = 0
        for t in list_threads():
            created = str(getattr(t, "created_at", "") or "")
            if created < cutoff:
                continue
            desc = str(getattr(t, "description", "") or "")
            if "auto-opened by recovery_loop" in desc:
                count += 1
        return count
    except Exception:
        return 0


def maybe_open_thread(
    *,
    question: str,
    failure_summary: str = "",
    triggering_request_id: Optional[str] = None,
) -> dict[str, Any]:
    """Conditionally open a Q8 thread for a hard question.

    Called by the Recovery Loop after every exhausted refusal-recovery
    attempt. Returns a structured result:

      * ``{"opened": True, "thread_id": "..."}``
      * ``{"opened": False, "reason": "switch_off|too_short|dedup|rate_limit|module_unavailable"}``

    Failure-isolated. The Recovery Loop's main path NEVER waits on
    this — auto_thread runs as a fire-and-forget enhancement.
    """
    if not _enabled():
        return {"opened": False, "reason": "switch_off"}
    q = (question or "").strip()
    if len(q) < _MIN_QUESTION_CHARS:
        return {"opened": False, "reason": "too_short"}
    if _has_similar_open_thread(q):
        return {"opened": False, "reason": "dedup"}
    if _count_recent_auto_threads() >= _MAX_THREADS_PER_DAY:
        return {"opened": False, "reason": "rate_limit"}

    try:
        from app.threads.lifecycle import create_thread
    except Exception:
        return {"opened": False, "reason": "module_unavailable"}

    title = f"Hard question: {q[:80]}"
    description_parts = [
        "Auto-opened by recovery_loop. The Commander + Recovery Loop "
        "both failed to fully answer this question. Tracking as a "
        "long-horizon line of inquiry.",
    ]
    if failure_summary:
        description_parts.append(f"Failure summary:\n{failure_summary}")
    if triggering_request_id:
        description_parts.append(f"Triggering request: {triggering_request_id}")
    try:
        thread = create_thread(
            title=title,
            description="\n\n".join(description_parts),
        )
        thread_id = getattr(thread, "id", None)
        return {"opened": True, "thread_id": thread_id}
    except Exception as exc:
        logger.debug("auto_thread: create_thread raised %r", exc, exc_info=True)
        return {"opened": False, "reason": f"create_failed:{type(exc).__name__}"}


__all__ = ["maybe_open_thread"]
