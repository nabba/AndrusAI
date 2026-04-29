"""Manual judge pins — operator overrides for the dynamic cross-eval rotation.

The ``_discover_judges`` rotation in :mod:`app.llm_discovery` picks the
highest-intelligence catalog entry per provider family (Anthropic /
Google / OpenAI / xAI / etc.) for cross-evaluation bias prevention. A
row in ``control_plane.judge_pins`` overrides that pick for a specific
provider family — useful when:

  * The dynamically-picked judge is too slow / expensive in practice.
  * Operator wants reproducibility across re-benchmark runs.
  * A specific model has shown bias and you want a specific fallback.

The rotation reads pins first; if no pin exists for a family, the
dynamic intelligence-ranked pick wins. This keeps the system
self-improving (new frontier models become judges automatically) while
giving operators a hard override when needed.

Schema: ``migrations/025_judge_pins_and_evaluations.sql``.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)


# ── Cache ────────────────────────────────────────────────────────────────
# 5-second TTL like ``llm_role_assignments`` — pins change at human
# cadence so a short cache absorbs hot-path lookups without staleness.

_CACHE_TTL_S = 5.0
_cache: tuple[float, dict[str, str]] | None = None
_cache_lock = threading.Lock()


def _invalidate() -> None:
    global _cache
    with _cache_lock:
        _cache = None


def list_pins() -> dict[str, str]:
    """Return ``{provider_family: model}`` for every active pin.

    Reads through a 5-second cache to keep judge selection cheap on the
    hot path. Returns an empty dict when the DB is unreachable so the
    discovery system falls through to the dynamic rotation.
    """
    global _cache
    now = time.monotonic()
    with _cache_lock:
        if _cache and (now - _cache[0]) < _CACHE_TTL_S:
            return _cache[1]
    try:
        from app.control_plane.db import execute
        rows = execute(
            "SELECT provider_family, model FROM control_plane.judge_pins",
            (),
            fetch=True,
        ) or []
        out = {r["provider_family"]: r["model"] for r in rows}
    except Exception as exc:
        logger.debug("llm_judge_pins.list_pins failed: %s", exc)
        out = {}
    with _cache_lock:
        _cache = (time.monotonic(), out)
    return out


def list_pins_detailed() -> list[dict[str, Any]]:
    """Full-row variant for the dashboard. Includes pinned_by / reason / pinned_at."""
    try:
        from app.control_plane.db import execute
        return execute(
            """
            SELECT provider_family, model, pinned_by, reason, pinned_at
              FROM control_plane.judge_pins
          ORDER BY provider_family
            """,
            (),
            fetch=True,
        ) or []
    except Exception as exc:
        logger.debug("llm_judge_pins.list_pins_detailed failed: %s", exc)
        return []


def pin_judge(
    provider_family: str,
    model: str,
    *,
    pinned_by: str = "user",
    reason: str = "",
) -> bool:
    """Pin ``model`` as the judge for ``provider_family``. Returns True on success.

    Validates ``model`` is in the live catalog so we never write a pin
    pointing at a stale key. Idempotent — re-pinning the same family
    overwrites the prior row (rather than producing duplicates).
    """
    try:
        from app.llm_catalog import CATALOG
        if model not in CATALOG:
            logger.warning(
                "llm_judge_pins: refusing to pin %s -> %r — not in CATALOG (%d entries)",
                provider_family, model, len(CATALOG),
            )
            return False
        from app.control_plane.db import execute
        execute(
            """
            INSERT INTO control_plane.judge_pins
                   (provider_family, model, pinned_by, reason)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (provider_family) DO UPDATE SET
                model     = EXCLUDED.model,
                pinned_by = EXCLUDED.pinned_by,
                reason    = EXCLUDED.reason,
                pinned_at = NOW()
            """,
            (provider_family, model, pinned_by, reason or None),
        )
        _invalidate()
        logger.info(
            "llm_judge_pins: pinned family=%s -> %s by %s",
            provider_family, model, pinned_by,
        )
        return True
    except Exception as exc:
        logger.warning("llm_judge_pins.pin_judge failed: %s", exc)
        return False


def unpin_judge(provider_family: str) -> bool:
    """Remove the pin for ``provider_family``. Returns True if a row was removed."""
    try:
        from app.control_plane.db import execute
        rows = execute(
            "DELETE FROM control_plane.judge_pins WHERE provider_family = %s RETURNING provider_family",
            (provider_family,),
            fetch=True,
        ) or []
        _invalidate()
        if rows:
            logger.info("llm_judge_pins: unpinned family=%s", provider_family)
            return True
        return False
    except Exception as exc:
        logger.warning("llm_judge_pins.unpin_judge failed: %s", exc)
        return False
