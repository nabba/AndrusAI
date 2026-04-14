"""
config_api.py — Configuration management endpoints.

Extracted from main.py. Handles LLM mode switching.
"""

import hmac
import logging
import threading
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter(tags=["config"])

# Rate limiter for config endpoints — max 5 changes per minute
_config_rate_bucket: list = []
_config_rate_lock = threading.Lock()


def _config_rate_check() -> bool:
    """Return True if within rate limit for config changes."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=1)
    with _config_rate_lock:
        _config_rate_bucket[:] = [t for t in _config_rate_bucket if t > cutoff]
        if len(_config_rate_bucket) >= 5:
            return False
        _config_rate_bucket.append(now)
        return True


def verify_gateway_secret(request: Request) -> bool:
    """Verify the forwarder is authenticated with the gateway secret."""
    from app.config import get_gateway_secret
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    token = auth[7:]
    return hmac.compare_digest(token, get_gateway_secret())


@router.post("/llm_mode")
async def set_llm_mode_endpoint(request: Request):
    """Switch LLM mode (local/cloud/hybrid/insane)."""
    if not verify_gateway_secret(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not _config_rate_check():
        raise HTTPException(status_code=429, detail="Too many config changes. Try again later.")
    payload = await request.json()
    mode = payload.get("mode", "").strip().lower()
    if mode not in ("local", "cloud", "hybrid", "insane"):
        raise HTTPException(status_code=400, detail="Invalid mode. Use: local, cloud, hybrid, insane")
    from app.llm_mode import set_mode
    from app.firebase_reporter import report_llm_mode
    set_mode(mode)
    report_llm_mode(mode)
    return {"status": "ok", "mode": mode}


@router.get("/creative_mode")
async def get_creative_mode_endpoint():
    """Return current creative-mode runtime settings (budget, originality weight)."""
    from app.creative_mode import snapshot
    return snapshot()


@router.post("/creative_mode")
async def set_creative_mode_endpoint(request: Request):
    """Update creative-mode runtime settings.

    Accepts any subset of: creative_run_budget_usd (float),
    originality_wiki_weight (float in [0, 1]).
    """
    if not verify_gateway_secret(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not _config_rate_check():
        raise HTTPException(status_code=429, detail="Too many config changes. Try again later.")
    payload = await request.json()
    from app.creative_mode import (
        set_budget_usd, set_originality_wiki_weight, snapshot,
    )

    if "creative_run_budget_usd" in payload:
        try:
            set_budget_usd(float(payload["creative_run_budget_usd"]))
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    if "originality_wiki_weight" in payload:
        try:
            set_originality_wiki_weight(float(payload["originality_wiki_weight"]))
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    return {"status": "ok", **snapshot()}
