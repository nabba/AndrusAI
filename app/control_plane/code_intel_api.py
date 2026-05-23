"""Code-intel REST surface at /api/cp/code-intel (Phase C.5, 2026-05-22).

Read-only operator visibility into the JSONL symbol index. One
endpoint for now (``/stats``) — surfaces what's on disk without
shell access. Future surfaces (query passthrough, manual refresh
trigger) can live here.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends

from app.control_plane.auth_dep import require_gateway_auth

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/cp/code-intel",
    tags=["control-plane", "code-intel"],
    dependencies=[Depends(require_gateway_auth)],
)


def _safe_master_switch() -> bool:
    try:
        from app import runtime_settings
        return runtime_settings.get_code_intel_enabled()
    except Exception:
        return False


def _safe_stats() -> dict[str, Any]:
    try:
        from app.code_intel.store import stats
        return stats()
    except Exception:
        logger.debug(
            "code_intel_api: stats read failed", exc_info=True,
        )
        return {
            "built": False,
            "symbols_count": 0,
            "references_count": 0,
            "indexed_files_count": 0,
            "symbols_bytes": 0,
            "references_bytes": 0,
            "indexed_at": "",
            "age_seconds": None,
        }


@router.get("/stats")
def stats_endpoint() -> dict[str, Any]:
    """Return the JSONL store's current state.

    Includes the master switch so operators can tell at a glance
    whether the index is just stale (switch ON, age large) vs.
    intentionally cold (switch OFF, never built).
    """
    s = _safe_stats()
    s["enabled"] = _safe_master_switch()
    return s
