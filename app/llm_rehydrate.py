"""
llm_rehydrate.py — Replay promoted models into the runtime CATALOG.

``llm_discovery._add_to_runtime_catalog`` only mutates the in-memory
``CATALOG``. On process restart that mutation is gone — the DB still
remembers the promotion, but the catalog does not. This module bridges
the gap: it reads every ``discovered_models`` row with
``status = 'promoted'`` and re-inserts the entry into ``CATALOG``.

Called from three places:
  - ``app.idle_scheduler.start()`` on process boot
  - ``app.llm_catalog_builder.refresh()`` after a catalog rebuild
  - ``app.llm_discovery.consume_approved_promotions`` on every
    governance-approved promotion (``force=True``)

Idempotent — safe to call multiple times; existing catalog entries
are never overwritten.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

_rehydrated = False

# Mirrors the provider dispatch in app.llm_factory._construct_from_entry —
# the only two providers the factory knows how to build. A promoted row
# with any other provider (e.g. a pre-consolidation "anthropic" row) would
# raise ConstructionFailed("unknown_provider", ...) on every selection
# attempt if rehydrated; see the 2026-07-24 refusal check below.
_VALID_CATALOG_PROVIDERS = frozenset({"openrouter", "ollama"})


def rehydrate_catalog(force: bool = False, *, retries: int = 3, retry_sleep: float = 2.0) -> int:
    """Load every promoted discovered_models row into CATALOG.

    Parameters
    ----------
    force : bool
        Re-run even if a prior call already succeeded in this process.
    retries : int
        Number of attempts when the DB query returns None (pool not
        ready yet — common during cold boot when PostgreSQL is still
        warming up). Each retry waits ``retry_sleep`` seconds.
    retry_sleep : float
        Delay between retries in seconds.

    Returns the number of entries added. Always logs the final outcome
    (success + count, or the reason it gave up) so a silent failure on
    boot shows up in the gateway logs instead of being invisible.
    """
    global _rehydrated
    if _rehydrated and not force:
        return 0

    try:
        from app.control_plane.db import execute
    except Exception as exc:
        logger.warning(f"rehydrate_catalog: control_plane.db import failed: {exc}")
        return 0

    rows = None
    for attempt in range(1, max(1, retries) + 1):
        try:
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
            )
        except Exception as exc:
            logger.debug(f"rehydrate_catalog: query attempt {attempt} raised: {exc}")
            rows = None
        if rows is not None:
            break
        if attempt < retries:
            logger.info(
                f"rehydrate_catalog: DB not ready (attempt {attempt}/{retries}), "
                f"retrying in {retry_sleep}s"
            )
            time.sleep(retry_sleep)

    if rows is None:
        logger.warning(
            "rehydrate_catalog: DB unreachable after %d attempts — promoted "
            "models will not be in CATALOG until the next idle refresh",
            retries,
        )
        return 0

    if not rows:
        _rehydrated = True
        logger.info("rehydrate_catalog: no promoted models on record")
        return 0

    try:
        from app.llm_discovery import _add_to_runtime_catalog
        from app.llm_catalog import CATALOG
    except Exception as exc:
        logger.warning(f"rehydrate_catalog: import failed: {exc}")
        return 0

    added = 0
    skipped = 0
    invalid_provider = 0
    for row in rows:
        # Key shape matches _add_to_runtime_catalog's expectations.
        model_id = row["model_id"]
        key = model_id.split("/")[-1] if "/" in model_id else model_id
        if key in CATALOG:
            skipped += 1
            continue
        # 2026-07-24: a promoted row surviving from before the
        # OpenRouter+Ollama consolidation (2026-05-29) can carry
        # provider="anthropic" (or any other pre-consolidation value).
        # app.llm_factory._construct_from_entry only knows how to build
        # "openrouter" and "ollama" entries — anything else raises
        # ConstructionFailed("unknown_provider", ...) on EVERY selection
        # attempt for that model's role, silently wasting a construction
        # and falling back further down the chain (see
        # reports/ANSWER_QUALITY_DIAGNOSIS_2026-07-24.md, finding D1).
        # Refuse to rehydrate what the factory can never build.
        row_provider = row.get("provider") or "openrouter"
        if row_provider not in _VALID_CATALOG_PROVIDERS:
            invalid_provider += 1
            logger.warning(
                "rehydrate_catalog: skipping %s — stale provider %r is not "
                "constructable (valid: %s); demote/delete this row in "
                "control_plane.discovered_models",
                key, row_provider, sorted(_VALID_CATALOG_PROVIDERS),
            )
            continue
        model_payload = {
            "model_id":          model_id,
            "provider":          row_provider,
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
    # Always log final count so silent boot failures are visible.
    logger.info(
        "rehydrate_catalog: restored %d promoted model(s) into CATALOG "
        "(%d already present, %d invalid-provider skipped, %d total on record)",
        added, skipped, invalid_provider, len(rows),
    )
    return added


def is_rehydrated() -> bool:
    return _rehydrated
