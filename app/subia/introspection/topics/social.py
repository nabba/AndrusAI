"""Social-model topic — Phase 8 Theory-of-Mind for Andrus + agents.

When user asks "what do you think I want?" or "how well do you know
me?", surface SocialModelEntry data: inferred_focus, expectations,
trust_level, divergences detected.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def gather() -> dict:
    out: dict = {
        "entities": [],
        "kernel_active": False,
    }
    try:
        from app.subia.live_integration import get_last_state
        live = get_last_state()
        kernel = getattr(live, "kernel", None) if live else None
        if kernel is None:
            return out
        out["kernel_active"] = True
        for entity_id, model in (kernel.social_models or {}).items():
            out["entities"].append({
                "entity_id": entity_id,
                "entity_type": getattr(model, "entity_type", "human"),
                "inferred_focus": list(
                    getattr(model, "inferred_focus", []) or []
                )[:8],
                "inferred_expectations": list(
                    getattr(model, "inferred_expectations", []) or []
                )[:6],
                "inferred_priorities": list(
                    getattr(model, "inferred_priorities", []) or []
                )[:6],
                "trust_level": round(
                    float(getattr(model, "trust_level", 0.5) or 0.5), 3,
                ),
                "last_interaction": getattr(model, "last_interaction", "") or "",
                "divergences": list(
                    getattr(model, "divergences", []) or []
                )[-5:],
            })
    except Exception as exc:
        logger.debug("introspection.social: gather failed: %s", exc)
    return out


def format_section(data: dict) -> str:
    if not data:
        return ""
    lines = ["## Theory-of-Mind / social models (Phase 8)"]
    if not data.get("kernel_active"):
        lines.append(
            "(Kernel not yet running — social models populate after CIL "
            "loops execute. The model of Andrus is updated periodically "
            "by social_model.update_from_interaction at Step 6.)"
        )
        return "\n".join(lines)
    if not data["entities"]:
        lines.append(
            "(Kernel running but no social models populated yet — "
            "Phase 8 update fires every NARRATIVE_DRIFT_CHECK_FREQUENCY "
            "loops; needs interaction history.)"
        )
        return "\n".join(lines)

    for e in data["entities"]:
        lines.append(
            f"Model of {e['entity_id']} ({e['entity_type']}):"
        )
        lines.append(
            f"  - trust_level: {e['trust_level']}"
        )
        if e["inferred_focus"]:
            lines.append(
                f"  - inferred_focus: {', '.join(str(x)[:50] for x in e['inferred_focus'])}"
            )
        if e["inferred_expectations"]:
            lines.append(
                "  - inferred_expectations: "
                + ", ".join(str(x)[:50] for x in e["inferred_expectations"])
            )
        if e["inferred_priorities"]:
            lines.append(
                "  - inferred_priorities: "
                + ", ".join(str(x)[:50] for x in e["inferred_priorities"])
            )
        if e["divergences"]:
            lines.append(
                "  - recent divergences (where my model and observed "
                "behaviour disagree):"
            )
            for d in e["divergences"][:3]:
                lines.append(f"      • {str(d)[:140]}")
        if e["last_interaction"]:
            lines.append(f"  - last_interaction: {e['last_interaction']}")

    lines.append(
        "These ToM models are based on BEHAVIOURAL evidence only "
        "(Phase 8 design). When asked 'what do I want?' I should "
        "answer from inferred_focus + priorities, with appropriate "
        "epistemic humility about how stale the inference is."
    )
    return "\n".join(lines)
