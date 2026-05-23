"""skills_used — recent skill invocations + a suggested next one.

The Hermes-style skill registry (app/skills/) accumulates "save this
workflow" entries. This section surfaces:

  * 2-3 skills invoked in the last 7 days (what the operator has
    actually been using)
  * The single oldest skill that's never been invoked (a nudge to
    either run it or delete it — keeps the registry honest)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

ID = "skills-used"
DISPLAY_NAME = "🪛 Skills (recent + dormant)"
DESCRIPTION = (
    "Skills invoked in the last 7 days plus one dormant skill nudge — "
    "keeps the saved-workflow registry honest by surfacing what you "
    "use vs what's gathering dust."
)


def gather() -> list[str]:
    try:
        from app.skills import store
    except Exception:
        logger.debug("skills_used: store import failed", exc_info=True)
        return []
    skills: list = []
    try:
        if hasattr(store, "list_all"):
            skills = store.list_all() or []
        elif hasattr(store, "all"):
            skills = list(store.all() or [])
    except Exception:
        logger.debug("skills_used: list failed", exc_info=True)
        return []
    if not skills:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    recent: list[tuple[datetime, str]] = []
    dormant: list[tuple[datetime | None, str]] = []
    for sk in skills:
        if isinstance(sk, dict):
            name = sk.get("name") or sk.get("id") or "?"
            last = sk.get("last_run_at") or sk.get("last_used")
            uses = int(sk.get("uses") or sk.get("invocations") or 0)
            created = sk.get("created_at") or sk.get("created")
        else:
            name = getattr(sk, "name", "") or getattr(sk, "id", "?")
            last = getattr(sk, "last_run_at", "") or getattr(sk, "last_used", "")
            uses = int(getattr(sk, "uses", 0) or getattr(sk, "invocations", 0) or 0)
            created = getattr(sk, "created_at", "") or getattr(sk, "created", "")
        last_dt = None
        if last:
            try:
                last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
            except ValueError:
                last_dt = None
        if last_dt and last_dt >= cutoff:
            recent.append((last_dt, str(name)[:50]))
        elif uses == 0:
            created_dt = None
            if created:
                try:
                    created_dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                    if created_dt.tzinfo is None:
                        created_dt = created_dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    pass
            dormant.append((created_dt, str(name)[:50]))
    out: list[str] = []
    recent.sort(key=lambda x: x[0], reverse=True)
    for _, name in recent[:3]:
        out.append(f"  • used: {name}")
    if dormant:
        dormant.sort(key=lambda x: x[0] or datetime.min.replace(tzinfo=timezone.utc))
        out.append(f"  • dormant nudge: {dormant[0][1]} (never invoked)")
    return out
