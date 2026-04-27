"""Scene topic — focal items, peripheral, attention justification, specious-present.

What's actively in the system's workspace right now: focal items
(GWT-2 admissions), peripheral tier (Phase 5), attention justification
(why these items won the competition), and specious-present
retention (what just dropped out + protention forecast).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def gather(*, max_focal: int = 5, max_peripheral: int = 5) -> dict:
    out: dict = {
        "focal_items": [],
        "peripheral_items": [],
        "attention_justification": {},
        "lingering_items": [],
        "stable_items": [],
        "tempo": 0.0,
        "direction": "",
        "kernel_active": False,
    }
    try:
        from app.subia.live_integration import get_last_state
        live = get_last_state()
        kernel = getattr(live, "kernel", None) if live else None
        if kernel is None:
            return out

        out["kernel_active"] = True
        for it in (kernel.focal_scene() or [])[:max_focal]:
            out["focal_items"].append({
                "id": getattr(it, "id", ""),
                "summary": str(getattr(it, "summary", ""))[:120],
                "salience": round(float(getattr(it, "salience", 0.0)), 3),
                "ownership": getattr(it, "ownership", "self"),
                "processing_mode": getattr(it, "processing_mode", None),
                "wonder_intensity": round(
                    float(getattr(it, "wonder_intensity", 0.0)), 3,
                ),
            })
        for it in (kernel.peripheral_scene() or [])[:max_peripheral]:
            out["peripheral_items"].append({
                "id": getattr(it, "id", ""),
                "summary": str(getattr(it, "summary", ""))[:80],
                "salience": round(float(getattr(it, "salience", 0.0)), 3),
            })

        try:
            out["attention_justification"] = dict(
                getattr(kernel.meta_monitor, "attention_justification", {}) or {}
            )
        except Exception:
            pass

        # Specious-present derived sets (Phase 14)
        try:
            sp = getattr(kernel, "specious_present", None)
            if sp is not None:
                out["lingering_items"] = sorted(sp.lingering_items())[:max_focal]
                out["stable_items"] = sorted(sp.stable_items())[:max_focal]
                out["tempo"] = round(float(getattr(sp, "tempo", 0.0)), 3)
                out["direction"] = getattr(sp, "direction", "") or ""
        except Exception:
            pass
    except Exception as exc:
        logger.debug("introspection.scene: gather failed: %s", exc)
    return out


def format_section(data: dict) -> str:
    if not data:
        return ""
    if not data.get("kernel_active"):
        return (
            "## Current scene / attention\n"
            "(No CIL loops have run yet — the kernel scene is empty. "
            "Scene state populates after the first crew task runs.)"
        )
    lines = ["## Current scene / attention (Phase 5 + Phase 14)"]

    if data.get("focal_items"):
        lines.append("Focal items (in active workspace right now):")
        for it in data["focal_items"]:
            mode = it.get("processing_mode")
            mode_str = f" mode={mode}" if mode else ""
            wonder = it.get("wonder_intensity") or 0.0
            wonder_str = f" wonder={wonder}" if wonder > 0 else ""
            lines.append(
                f"  - [{it['id']}] \"{it['summary'][:80]}\" "
                f"(salience={it['salience']}, "
                f"ownership={it['ownership']}{mode_str}{wonder_str})"
            )
    else:
        lines.append("Focal items: (none — workspace currently empty)")

    if data.get("peripheral_items"):
        lines.append(
            f"Peripheral tier ({len(data['peripheral_items'])} items "
            "— metadata-only, not in focus):"
        )
        for it in data["peripheral_items"][:3]:
            lines.append(
                f"  - [{it['id']}] {it['summary'][:60]}..."
            )

    if data.get("attention_justification"):
        lines.append("Attention justification (why these items won the gate):")
        for item_id, reason in list(data["attention_justification"].items())[:3]:
            lines.append(f"  - {item_id}: {str(reason)[:120]}")

    if data.get("lingering_items"):
        lines.append(
            "Specious-present lingering items "
            "(just dropped out of focus but still 'echoing'):"
        )
        lines.append(f"  - {', '.join(data['lingering_items'][:5])}")

    if data.get("stable_items"):
        lines.append(
            "Stable items (present across the entire retention window):"
        )
        lines.append(f"  - {', '.join(data['stable_items'][:5])}")

    if data.get("tempo") or data.get("direction"):
        lines.append(
            f"Specious-present feel: tempo={data.get('tempo',0.0)}, "
            f"direction={data.get('direction','stable')}"
        )
    return "\n".join(lines)
