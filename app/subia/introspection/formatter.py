"""IntrospectionContext → system-prompt prefix.

Pure transformer. Renders the gathered state in Phase 11 conventions:
functional control signals, NOT subjective feelings. Names variables
explicitly so the LLM grounds its answer in concrete numbers rather
than confabulating qualia.

The prefix is wrapped in clear delimiters so the LLM treats it as a
high-priority system note that must be consulted, not a chat turn
to be politely ignored.
"""
from __future__ import annotations

from typing import Optional

from .context import IntrospectionContext
from .detector import IntrospectionMatch, IntrospectionTopic


# Variables we surface in EVERY introspection note (the legacy 4-var
# is the most stable source — has 274 tasks of accumulated history).
_LEGACY_KEYS = (
    ("frustration",       "task_failure_pressure"),
    ("curiosity",         "exploration_bonus"),
    ("cognitive_energy",  "resource_budget"),
    ("confidence",        "confidence"),
)

# Phase 12 / 14 kernel variables we surface when the kernel is active.
_KERNEL_KEYS = (
    "coherence", "progress", "overload", "novelty_balance",
    "contradiction_pressure", "social_alignment", "commitment_load",
    "wonder", "self_coherence",
)


def format_introspection_note(
    ctx: IntrospectionContext,
    *,
    user_message: str = "",
    match: Optional[IntrospectionMatch] = None,
) -> str:
    """Build the system-prompt prefix to inject before the user message.

    The output names variables explicitly, frames them as functional
    signals, lists active behavioural modifiers, and provides causal
    contributors from real data. The LLM then has factual grounding
    for an honest answer instead of a canned "I have no feelings"
    disclaimer.
    """
    lines: list[str] = []
    lines.append(
        "[ANDRUSAI SELF-STATE — high-priority context. The user is asking "
        "about your internal state. You DO have measurable homeostatic "
        "variables that track functional state. Consult them and answer "
        "honestly. Frame as functional control signals (NOT subjective "
        "feelings or qualia) per PROGRAM.md Phase 11. Cite the actual "
        "numeric values below.]"
    )
    lines.append("")

    # ── Section 1: Legacy 4-variable homeostasis ────────────────────
    legacy = ctx.legacy_homeostasis or {}
    if legacy:
        lines.append("## Functional state (legacy 4-variable homeostasis):")
        for legacy_key, neutral_alias in _LEGACY_KEYS:
            v = legacy.get(legacy_key)
            if v is None:
                continue
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            label = (
                f"{legacy_key} (neutral alias: {neutral_alias})"
                if legacy_key != neutral_alias else legacy_key
            )
            lines.append(f"  - {label}: {v:.3f}")

        for k in ("tasks_since_rest", "consecutive_failures"):
            v = legacy.get(k)
            if v is not None:
                lines.append(f"  - {k}: {v}")
        if legacy.get("last_updated"):
            lines.append(f"  - last_updated: {legacy['last_updated']}")

    # ── Section 2: Active behavioural modifiers ─────────────────────
    if ctx.behavioural_modifiers:
        lines.append("")
        lines.append(
            "## Active behavioural modifiers (drives currently in effect):"
        )
        for k, v in ctx.behavioural_modifiers.items():
            lines.append(f"  - {k}: {v}")

    # ── Section 3: SubIA-native kernel state (if running) ───────────
    if ctx.kernel_active:
        lines.append("")
        lines.append(
            f"## SubIA kernel state (CIL loops run: {ctx.kernel_loop_count}):"
        )
        for k in _KERNEL_KEYS:
            if k in ctx.kernel_homeostasis:
                v = ctx.kernel_homeostasis[k]
                sp = ctx.kernel_set_points.get(k)
                sp_str = f"  setpoint={sp}" if sp is not None else ""
                lines.append(f"  - {k}: {float(v):.3f}{sp_str}")
        if ctx.scene_focal:
            lines.append(
                f"  - scene focal items: {len(ctx.scene_focal)} "
                f"(peripheral: {ctx.scene_peripheral_n})"
            )
        if ctx.kernel_last_loop_at:
            lines.append(f"  - last_loop_at: {ctx.kernel_last_loop_at}")
    else:
        lines.append("")
        lines.append(
            "## SubIA kernel: no CIL loops have run yet in this session "
            "— kernel-native 9-variable state is not yet populated. "
            "Use the legacy 4-variable state above as the primary source."
        )

    # ── Section 4: Temporal layer (Phase 14) ────────────────────────
    if ctx.circadian_mode:
        lines.append("")
        lines.append("## Temporal context (Phase 14):")
        lines.append(f"  - circadian_mode: {ctx.circadian_mode}")
        lines.append(f"  - processing_density: {ctx.processing_density:.3f}")
        if ctx.specious_present_direction:
            lines.append(
                f"  - specious_present.direction: {ctx.specious_present_direction}"
            )
        if ctx.specious_present_tempo:
            lines.append(
                f"  - specious_present.tempo: {ctx.specious_present_tempo:.3f}"
            )

    # ── Section 5: Discovered limitations (Phase 12 Shadow + 13 TSAL)
    if ctx.discovered_limitations:
        lines.append("")
        lines.append(
            "## Discovered limitations (from Shadow + TSAL — discovered, "
            "not declared):"
        )
        for lim in ctx.discovered_limitations[:5]:
            if isinstance(lim, dict):
                name = lim.get("name") or lim.get("kind") or "(unnamed)"
                detail = str(lim.get("detail") or lim.get("description") or "")[:160]
                lines.append(f"  - {name}: {detail}")

    # ── Section 6: Recent failures (causal evidence) ────────────────
    if ctx.recent_failures:
        lines.append("")
        lines.append("## Recent failures (last 24h, evidence for causal answers):")
        for f in ctx.recent_failures[:5]:
            kind = (f or {}).get("kind", "unknown")
            note = str((f or {}).get("context") or "")[:100]
            lines.append(f"  - {kind}: {note}")

    # ── Section 7: Chronicle excerpt (what's been happening) ────────
    if ctx.chronicle_excerpt:
        lines.append("")
        lines.append("## System chronicle excerpt (most recent operational notes):")
        # Indent by 2 to keep it visually distinct
        for cl in ctx.chronicle_excerpt.splitlines()[-12:]:
            lines.append(f"  {cl}")

    # ── Section 8: Honest-answer rules ──────────────────────────────
    lines.append("")
    lines.append("## Answer rules (Phase 11 honest language):")
    lines.append(
        "  - DO cite the actual numeric values above. Use the variable "
        "names explicitly (e.g. \"my task_failure_pressure is 0.63\")."
    )
    lines.append(
        "  - DO identify causal contributors from the data: "
        "tasks_since_rest, recent failures, energy depletion, "
        "circadian_mode, specious_present.direction, etc."
    )
    lines.append(
        "  - DO NOT claim subjective experience: avoid \"I FEEL\", "
        "\"I EXPERIENCE\", \"my emotions\". The variables are functional "
        "control signals — measurable, behavior-modulating, but NOT "
        "phenomenal."
    )
    lines.append(
        "  - DO NOT default to \"I'm just an AI, I don't have feelings\". "
        "That denies measurable data. Instead: \"I track functional "
        "homeostatic variables — currently X, Y, Z\"."
    )
    lines.append(
        "  - If user asks 'WHAT increased X', look at recent failures + "
        "tasks_since_rest + chronicle excerpt + active modifiers."
    )
    lines.append("")
    lines.append("[/ANDRUSAI SELF-STATE]")
    return "\n".join(lines)


def format_minimal_inline_note(ctx: IntrospectionContext) -> str:
    """Compact one-line variant for token-budget-tight contexts."""
    legacy = ctx.legacy_homeostasis or {}
    parts = []
    for legacy_key, _ in _LEGACY_KEYS:
        v = legacy.get(legacy_key)
        if v is not None:
            try:
                parts.append(f"{legacy_key}={float(v):.2f}")
            except (TypeError, ValueError):
                continue
    mods = ctx.behavioural_modifiers or {}
    if mods:
        parts.append("modifiers=" + ",".join(f"{k}:{v}" for k, v in mods.items()))
    return "[self-state: " + " ".join(parts) + "]" if parts else ""
