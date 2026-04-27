"""Wonder + Shadow topic — Phase 12 outputs.

Wonder Register events (depth-sensitive epistemic affect that has
fired) and Shadow Self findings (discovered behavioural biases).
Reverie-engine outputs are surfaced via wiki/meta/reverie/ pages.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# WONDER
# ─────────────────────────────────────────────────────────────────────

def gather_wonder(*, max_items: int = 6, max_reveries: int = 4) -> dict:
    out: dict = {
        "current_wonder_level": 0.0,
        "wonder_threshold": 0.0,
        "items_with_wonder": [],
        "recent_reveries": [],
        "kernel_active": False,
    }

    # Live wonder from kernel
    try:
        from app.subia.live_integration import get_last_state
        live = get_last_state()
        kernel = getattr(live, "kernel", None) if live else None
        if kernel is not None:
            out["kernel_active"] = True
            out["current_wonder_level"] = round(
                float(kernel.homeostasis.variables.get("wonder", 0.0) or 0.0),
                3,
            )
            try:
                from app.subia.connections.temporal_subia_bridge import (
                    effective_wonder_threshold,
                )
                out["wonder_threshold"] = round(
                    float(effective_wonder_threshold(kernel) or 0.3), 3,
                )
            except Exception:
                from app.subia.config import SUBIA_CONFIG
                out["wonder_threshold"] = float(
                    SUBIA_CONFIG.get("WONDER_INHIBIT_THRESHOLD", 0.3)
                )
            for it in (kernel.scene or [])[:max_items]:
                wi = float(getattr(it, "wonder_intensity", 0.0) or 0.0)
                if wi > 0:
                    out["items_with_wonder"].append({
                        "id": getattr(it, "id", ""),
                        "summary": str(getattr(it, "summary", ""))[:80],
                        "intensity": round(wi, 3),
                    })
    except Exception as exc:
        logger.debug("introspection.wonder: kernel gather failed: %s", exc)

    # Reverie pages from disk
    for root_candidate in (
        "/app/wiki/meta/reverie",
        "wiki/meta/reverie",
    ):
        try:
            root = Path(root_candidate)
            if not root.exists():
                continue
            pages = sorted(
                root.glob("*.md"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for p in pages[:max_reveries]:
                try:
                    content = p.read_text(encoding="utf-8", errors="ignore")
                    # Strip frontmatter; keep first 200 chars of body
                    body = content
                    if content.startswith("---"):
                        parts = content.split("---", 2)
                        body = parts[2] if len(parts) >= 3 else content
                    out["recent_reveries"].append({
                        "title": p.stem,
                        "preview": body.strip().replace("\n", " ")[:240],
                    })
                except Exception:
                    continue
            break
        except Exception:
            continue

    return out


def format_wonder_section(data: dict) -> str:
    if not data:
        return ""
    lines = ["## Wonder Register + Reverie outputs (Phase 12)"]
    if data.get("kernel_active"):
        lvl = data.get("current_wonder_level", 0.0)
        thr = data.get("wonder_threshold", 0.3)
        lines.append(
            f"Current wonder level: {lvl:.3f} "
            f"(effective threshold: {thr:.3f}; "
            f"{'ABOVE — attention freeze active' if lvl > thr else 'below threshold'})"
        )
    else:
        lines.append("Wonder level: (kernel not yet running)")

    if data.get("items_with_wonder"):
        lines.append("Items currently carrying wonder:")
        for it in data["items_with_wonder"][:5]:
            lines.append(
                f"  - [{it['id']}] \"{it['summary']}\" intensity={it['intensity']}"
            )
    else:
        lines.append("Items carrying wonder: (none right now)")

    if data.get("recent_reveries"):
        lines.append("Recent reverie-engine syntheses (idle mind-wandering output):")
        for r in data["recent_reveries"][:4]:
            lines.append(f"  - {r['title']}: {r['preview'][:160]}")
    else:
        lines.append(
            "Recent reverie syntheses: (none — reverie engine "
            "registered but adapters not yet wired to ChromaDB/Neo4j; "
            "see Phase 16a follow-up)"
        )
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────
# SHADOW
# ─────────────────────────────────────────────────────────────────────

def gather_shadow(*, max_findings: int = 12) -> dict:
    out: dict = {
        "discovered_limitations": [],
        "discovered_capabilities": [],
        "kernel_active": False,
    }
    try:
        from app.subia.live_integration import get_last_state
        live = get_last_state()
        kernel = getattr(live, "kernel", None) if live else None
        if kernel is None:
            return out
        out["kernel_active"] = True
        for lim in (kernel.self_state.discovered_limitations or [])[:max_findings]:
            if isinstance(lim, dict):
                out["discovered_limitations"].append({
                    "name": lim.get("name", "(unnamed)"),
                    "kind": lim.get("kind", ""),
                    "detail": str(lim.get("detail") or "")[:160],
                    "discovered_at": lim.get("discovered_at", ""),
                })
        caps = kernel.self_state.capabilities or {}
        for k, v in caps.items():
            if isinstance(v, dict) and v.get("discovered"):
                out["discovered_capabilities"].append({
                    "name": k,
                    "value": {kk: vv for kk, vv in v.items()
                              if kk != "discovered"},
                })
    except Exception as exc:
        logger.debug("introspection.shadow: gather failed: %s", exc)
    return out


def format_shadow_section(data: dict) -> str:
    if not data:
        return ""
    lines = [
        "## Shadow Self — discovered limitations + capabilities (Phase 12 + 13)"
    ]
    if data.get("discovered_limitations"):
        lines.append("Discovered limitations (Phase 12 Shadow Self mining):")
        for f in data["discovered_limitations"][:8]:
            lines.append(
                f"  - [{f['kind']}] {f['name']}: {f['detail'][:120]}"
            )
    else:
        lines.append(
            "Discovered limitations: (none yet — Shadow Self runs "
            "monthly; needs sufficient behavioural history to mine biases)"
        )
    if data.get("discovered_capabilities"):
        lines.append(
            "Discovered capabilities (TSAL + rhythm discovery, "
            "discovered=True markers):"
        )
        for c in data["discovered_capabilities"][:8]:
            v = c.get("value", {})
            preview = ", ".join(f"{k}={vv}" for k, vv in list(v.items())[:3])
            lines.append(f"  - {c['name']}: {preview}")
    return "\n".join(lines)


# Aliases so the registry works either way
gather = gather_shadow
format_section = format_shadow_section
