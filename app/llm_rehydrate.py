"""
llm_rehydrate.py — Replay promoted models into the runtime CATALOG.

``llm_discovery._add_to_runtime_catalog`` only mutates the in-memory
``CATALOG``. On process restart that mutation is gone — the DB still
remembers the promotion, but the catalog does not. This module bridges
the gap: on startup it reads every ``discovered_models`` row with
``status = 'promoted'`` and re-inserts the entry into ``CATALOG``.

Idempotent — safe to call multiple times; existing catalog entries
are never overwritten.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_rehydrated = False


def rehydrate_catalog(force: bool = False) -> int:
    """Load every promoted discovered_models row into CATALOG.

    Returns the number of entries added (0 if already rehydrated,
    nothing found, or the DB is unreachable).
    """
    global _rehydrated
    if _rehydrated and not force:
        return 0

    try:
        from app.control_plane.db import execute
    except Exception:
        return 0

    rows = execute(
        """
        SELECT model_id, provider, display_name, context_window,
               cost_input_per_m, cost_output_per_m, multimodal,
               tool_calling, promoted_tier, promoted_roles,
               benchmark_score, benchmark_role
          FROM control_plane.discovered_models
         WHERE status = 'promoted'
        """,
        (),
        fetch=True,
    ) or []

    if not rows:
        _rehydrated = True
        return 0

    try:
        from app.llm_discovery import _add_to_runtime_catalog
        from app.llm_catalog import CATALOG
    except Exception as exc:
        logger.warning(f"rehydrate_catalog: import failed: {exc}")
        return 0

    added = 0
    for row in rows:
        # Key shape matches _add_to_runtime_catalog's expectations.
        model_id = row["model_id"]
        key = model_id.split("/")[-1] if "/" in model_id else model_id
        if key in CATALOG:
            continue
        model_payload = {
            "model_id":          model_id,
            "provider":          row.get("provider", "openrouter"),
            "display_name":      row.get("display_name", key),
            "context_window":    int(row.get("context_window") or 0),
            "cost_input_per_m":  float(row.get("cost_input_per_m") or 0),
            "cost_output_per_m": float(row.get("cost_output_per_m") or 0),
            "multimodal":        bool(row.get("multimodal")),
            "tool_calling":      bool(row.get("tool_calling")),
            "tier":              row.get("promoted_tier") or "budget",
            "benchmark_score":   float(row.get("benchmark_score") or 0.5),
        }
        roles = row.get("promoted_roles") or []
        # PostgreSQL TEXT[] comes back as a Python list already; defensive
        # parse for legacy string encodings.
        if isinstance(roles, str):
            roles = [r.strip() for r in roles.strip("{}").split(",") if r.strip()]
        try:
            _add_to_runtime_catalog(model_payload, list(roles) or ["research"])
            added += 1
        except Exception as exc:
            logger.warning(f"rehydrate_catalog: add {key} failed: {exc}")

    _rehydrated = True
    if added:
        logger.info(f"rehydrate_catalog: restored {added} promoted models into CATALOG")
    return added


def is_rehydrated() -> bool:
    return _rehydrated
