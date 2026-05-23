"""action_requests — pending operator-action items from app.action_requests.

The action_requests subsystem (PROGRAM §32) collects requests that
need operator decision — calendar-invite drafts, signal-send plans,
etc. — gated behind an operator approval. The morning briefing is a
good surface for the queue head."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

ID = "action-requests"
DISPLAY_NAME = "🛎 Pending action requests"
DESCRIPTION = (
    "Top 3 pending action requests from app/action_requests/. Things "
    "the system queued for your approval before acting."
)

_MAX_LINES = 3


def gather() -> list[str]:
    try:
        from app.action_requests import store
    except Exception:
        logger.debug("action_requests: store import failed", exc_info=True)
        return []
    rows: list = []
    try:
        if hasattr(store, "list_pending"):
            rows = store.list_pending() or []
        elif hasattr(store, "list_open"):
            rows = store.list_open() or []
        elif hasattr(store, "all"):
            rows = [r for r in store.all() if (getattr(r, "status", "") or "").lower() == "pending"]
    except Exception:
        logger.debug("action_requests: list failed", exc_info=True)
        return []
    if not rows:
        return []
    out: list[str] = []
    for r in rows[:_MAX_LINES]:
        if isinstance(r, dict):
            kind = r.get("action_type") or r.get("type") or ""
            summary = r.get("summary") or r.get("description") or r.get("title") or ""
        else:
            kind = getattr(r, "action_type", "") or getattr(r, "type", "")
            summary = getattr(r, "summary", "") or getattr(r, "description", "") or ""
        summary = (str(summary) or "")[:110]
        kind = (str(kind) or "request")[:30]
        out.append(f"  • [{kind}] {summary}")
    return out
