"""Beliefs topic — Phase 2 HOT-3 store + Phase 15 source registry + corrections.

Surfaces what the system actually believes (verified beliefs with
evidence), what it's suspended/retracted (asymmetric confirmation),
which sources it trusts for which topics, and recent user corrections.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def gather(*, max_beliefs: int = 12, max_sources: int = 12,
           max_corrections: int = 8) -> dict:
    out: dict = {
        "active_beliefs": [],
        "suspended_beliefs": [],
        "registered_sources": [],
        "recent_corrections": [],
        "available": False,
    }

    # ── Phase 2 belief store ────────────────────────────────────────
    try:
        from app.subia.belief.store import get_belief_store
        store = get_belief_store()
        actives = store.query_relevant(
            query="", domain=None, n=max_beliefs, min_confidence=0.0,
        ) or []
        for b in actives:
            status = getattr(b, "belief_status", "ACTIVE") or "ACTIVE"
            entry = {
                "domain": getattr(b, "domain", "") or "",
                "content": str(getattr(b, "content", ""))[:200],
                "confidence": round(float(getattr(b, "confidence", 0.0) or 0.0), 3),
                "evidence_count": len(getattr(b, "evidence_sources", []) or []),
                "status": status,
            }
            if status == "ACTIVE":
                out["active_beliefs"].append(entry)
            elif status in ("SUSPENDED", "RETRACTED", "SUPERSEDED"):
                out["suspended_beliefs"].append(entry)
        out["available"] = True
    except Exception as exc:
        logger.debug("introspection.beliefs: store gather failed: %s", exc)

    # ── Phase 15 source registry ────────────────────────────────────
    try:
        from app.subia.grounding.source_registry import get_default_registry
        reg = get_default_registry()
        for rs in (reg.all() or [])[:max_sources]:
            out["registered_sources"].append({
                "topic": rs.topic, "key": rs.key, "url": rs.url,
                "learned_from": rs.learned_from,
                "confidence": round(float(rs.confidence or 0.0), 3),
            })
    except Exception as exc:
        logger.debug("introspection.beliefs: registry gather failed: %s", exc)

    # ── Recent user corrections from narrative audit ────────────────
    try:
        from app.subia.safety.narrative_audit import read_audit_entries
        entries = read_audit_entries(limit=50) or []
        for e in entries:
            finding = getattr(e, "finding", "") or ""
            if "User correction" in finding or "correction_capture" in str(
                getattr(e, "sources", []) or []
            ):
                out["recent_corrections"].append({
                    "at": getattr(e, "at", ""),
                    "finding": finding[:240],
                })
                if len(out["recent_corrections"]) >= max_corrections:
                    break
    except Exception as exc:
        logger.debug("introspection.beliefs: audit gather failed: %s", exc)

    return out


def format_section(data: dict) -> str:
    if not data:
        return ""
    lines = ["## Beliefs / sources / corrections (Phase 2 HOT-3 + Phase 15)"]
    if data.get("active_beliefs"):
        lines.append("Active verified beliefs:")
        for b in data["active_beliefs"][:10]:
            lines.append(
                f"  - [{b['domain'] or '(no-domain)'}] "
                f"\"{b['content'][:100]}\" "
                f"(conf={b['confidence']}, evidence={b['evidence_count']})"
            )
    if data.get("suspended_beliefs"):
        lines.append("Suspended / retracted beliefs (epistemic humility — DO mention these):")
        for b in data["suspended_beliefs"][:5]:
            lines.append(
                f"  - [{b['domain']}] \"{b['content'][:100]}\" "
                f"(status={b['status']})"
            )
    if data.get("registered_sources"):
        lines.append("Authoritative sources I trust (discovered, not declared):")
        for rs in data["registered_sources"][:8]:
            lines.append(
                f"  - {rs['topic']}/{rs['key']} → {rs['url']} "
                f"(learned: {rs['learned_from']})"
            )
    if data.get("recent_corrections"):
        lines.append("Recent user corrections I've absorbed:")
        for c in data["recent_corrections"][:5]:
            lines.append(f"  - {c['at']}: {c['finding'][:160]}")
    if (not data.get("active_beliefs") and not data.get("suspended_beliefs")
            and not data.get("registered_sources")):
        lines.append("(no beliefs in store yet — system is in cold-start belief state)")
    return "\n".join(lines)
