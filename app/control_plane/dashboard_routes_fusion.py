"""Control-plane dashboard routes — fusion topic.

Read-only operator snapshot of OpenRouter Fusion: the master switch, scoped
roles, the *resolved* panel (the concrete model ids the catalog picks per
class — i.e. what the "LLM chooser" populated), the judge, caps, and today's
fusion spend. The mutating side is the standard ``/config/runtime_settings``
dispatcher (the eight ``fusion_*`` keys); this endpoint only resolves +
reflects so the React card and the main-page chip show exactly what will run.

Mounted via ``include_router`` in ``dashboard_api.py`` — the parent router
carries the ``/api/cp`` prefix and the ``require_gateway_auth`` dependency.
"""
import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)

# No prefix or dependencies here — the parent router supplies both.
router = APIRouter()


@router.get("/fusion/state")
def fusion_state_endpoint():
    """Resolved Fusion config for the operator surfaces (read-only)."""
    try:
        from app.fusion import fusion_state
        return fusion_state()
    except Exception as exc:  # noqa: BLE001
        logger.debug("fusion/state endpoint: %s", exc)
        return {
            "enabled": False,
            "active": False,
            "panel": [],
            "scope_roles": [],
            "error": str(exc)[:200],
        }


@router.get("/fusion/deliberations")
def fusion_deliberations_endpoint(limit: int = 20):
    """Recent fusion deliberations (judge analysis + final answer), newest first."""
    try:
        from app.fusion import recent_deliberations
        return {"deliberations": recent_deliberations(limit)}
    except Exception as exc:  # noqa: BLE001
        logger.debug("fusion/deliberations endpoint: %s", exc)
        return {"deliberations": [], "error": str(exc)[:200]}
