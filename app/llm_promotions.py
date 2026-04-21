"""
llm_promotions.py — Global promotion flag for LLM catalog entries.

Promotions are the middle tier of the three-layer selection authority:

    1. Pool        CATALOG membership only → resolver scores normally.
    2. Promoted    this module → resolver filters candidates to the
                   promoted subset whenever at least one promoted model
                   fits the role's hard constraints, then scores among
                   them.
    3. Hand-pinned role_assignments overlay, source='manual',
                   priority ≥ 1000 → resolver returns the pinned model
                   directly, no scoring.

Promotions are global (not per-role). A promoted ``kimi-k2.6`` becomes
first-choice for every role where it passes the tier / multimodal /
tool-support filters. Narrower preferences live in hand-pins.

The ``promote``/``demote`` pair is idempotent and drives a small
in-process cache (5 s TTL) so the resolver's hot path never hits the
DB more than once every few seconds.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Iterable

logger = logging.getLogger(__name__)

_CACHE_TTL_S = 5.0
_cache_lock = threading.Lock()
_cache_at: float = 0.0
_cached_set: set[str] = set()


def _refresh_cache() -> set[str]:
    """Fetch the current promoted-model set from PostgreSQL."""
    try:
        from app.control_plane.db import execute
        rows = execute(
            "SELECT model FROM control_plane.model_promotions",
            (),
            fetch=True,
        ) or []
        return {r["model"] for r in rows if r.get("model")}
    except Exception as exc:
        logger.debug(f"llm_promotions: fetch failed: {exc}")
        return set()


def invalidate_cache() -> None:
    """Drop the in-process cache. Call after every promote/demote."""
    global _cache_at, _cached_set
    with _cache_lock:
        _cache_at = 0.0
        _cached_set = set()


def list_promoted() -> set[str]:
    """Return the set of currently-promoted catalog keys (5 s TTL)."""
    global _cache_at, _cached_set
    now = time.monotonic()
    with _cache_lock:
        if (now - _cache_at) < _CACHE_TTL_S and _cached_set is not None:
            return set(_cached_set)
    fresh = _refresh_cache()
    with _cache_lock:
        _cached_set = fresh
        _cache_at = time.monotonic()
    return set(fresh)


def is_promoted(model: str) -> bool:
    """Return True iff ``model`` is in the promoted set."""
    if not model:
        return False
    return model in list_promoted()


def promote(
    model: str,
    *,
    promoted_by: str = "system",
    reason: str = "",
) -> bool:
    """Mark a catalog model as promoted. Idempotent.

    Rejected if ``model`` isn't present in the live CATALOG — prevents
    the dashboard from ever showing a promotion for a key the resolver
    can't score.
    """
    try:
        from app.llm_catalog import CATALOG
    except Exception:
        return False
    if model not in CATALOG:
        logger.warning(
            "llm_promotions: refusing to promote %r — not in live CATALOG",
            model,
        )
        return False
    try:
        from app.control_plane.db import execute
        execute(
            """
            INSERT INTO control_plane.model_promotions (model, promoted_by, reason, created_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (model) DO UPDATE SET
                promoted_by = EXCLUDED.promoted_by,
                reason      = EXCLUDED.reason,
                created_at  = NOW()
            """,
            (model, promoted_by, reason),
        )
        invalidate_cache()
        logger.info(
            "llm_promotions: promoted %s (by=%s, reason=%s)",
            model, promoted_by, reason or "—",
        )
        return True
    except Exception as exc:
        logger.warning(f"llm_promotions: promote({model!r}) failed: {exc}")
        return False


def demote(model: str) -> bool:
    """Remove a promotion. Idempotent (no-op if not promoted)."""
    try:
        from app.control_plane.db import execute
        execute(
            "DELETE FROM control_plane.model_promotions WHERE model = %s",
            (model,),
        )
        invalidate_cache()
        logger.info(f"llm_promotions: demoted {model}")
        return True
    except Exception as exc:
        logger.warning(f"llm_promotions: demote({model!r}) failed: {exc}")
        return False


def list_promotions_with_detail() -> list[dict]:
    """Full rows for dashboard / Signal display."""
    try:
        from app.control_plane.db import execute
        return execute(
            """
            SELECT model, promoted_by, reason, created_at
              FROM control_plane.model_promotions
          ORDER BY created_at DESC
            """,
            (),
            fetch=True,
        ) or []
    except Exception:
        return []


def format_promotions() -> str:
    """Human-readable summary (Signal command output)."""
    rows = list_promotions_with_detail()
    if not rows:
        return "No promoted models. Use `promote <model>` to add one."
    lines = [f"🚀 Promoted models ({len(rows)}):"]
    for r in rows:
        who = r.get("promoted_by", "?")
        reason = r.get("reason") or ""
        tail = f" ({reason})" if reason else ""
        lines.append(f"  · {r['model']}  by {who}{tail}")
    return "\n".join(lines)


# ── Bulk helpers for consume_approved_promotions ─────────────────────

def promote_many(models: Iterable[str], *, promoted_by: str, reason: str = "") -> int:
    """Promote multiple models in one pass. Returns count succeeded."""
    n = 0
    for m in models:
        if promote(m, promoted_by=promoted_by, reason=reason):
            n += 1
    return n
