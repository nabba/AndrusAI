"""IntrospectionContext — gather the state the formatter needs.

Pulls live data from FOUR sources, each in its own try/except so a
broken backend (e.g. legacy homeostasis JSON corrupt, kernel not yet
populated) degrades gracefully to empty fields rather than crashing
the chat path.

Sources:
  1. Legacy 4-variable homeostasis (app.subia.homeostasis.state)
     — frustration, curiosity, cognitive_energy, confidence + Phase 11
       neutral aliases (task_failure_pressure, exploration_bonus,
       resource_budget). 274 tasks of accumulated history live here.
  2. SubIA-native kernel (app.subia.live_integration._last_state)
     — 9-variable kernel homeostasis + scene + specious_present +
       temporal_context + meta_monitor.
  3. Behavioural modifiers (legacy state.get_behavioral_modifiers)
     — currently active drives (CAUTION, EFFICIENCY, THOROUGHNESS).
  4. Recent failures + chronicle excerpt (best-effort).

Tier-3 evaluators are NOT consulted directly — formatter renders from
the data only.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class IntrospectionContext:
    # Legacy 4-var (always populated when state file exists)
    legacy_homeostasis: dict = field(default_factory=dict)
    behavioural_modifiers: dict = field(default_factory=dict)

    # SubIA-native kernel (populated only after CIL has run)
    kernel_active: bool = False
    kernel_homeostasis: dict = field(default_factory=dict)
    kernel_set_points: dict = field(default_factory=dict)
    kernel_loop_count: int = 0
    kernel_last_loop_at: str = ""
    scene_focal: list = field(default_factory=list)
    scene_peripheral_n: int = 0

    # Phase 14 temporal layer
    circadian_mode: str = ""
    processing_density: float = 0.0
    specious_present_direction: str = ""
    specious_present_tempo: float = 0.0

    # Phase 9 + 12 + 13 derived state
    discovered_limitations: list = field(default_factory=list)
    discovered_capabilities: dict = field(default_factory=dict)

    # Operational evidence — recent failures
    recent_failures: list = field(default_factory=list)
    chronicle_excerpt: str = ""

    # Diagnostic — which sources successfully gathered
    sources_ok: list = field(default_factory=list)
    sources_failed: list = field(default_factory=list)


def gather_context() -> IntrospectionContext:
    """Read all four sources defensively. Never raises.

    Each gather is wrapped — a failure in one source records the
    failure in `sources_failed` and continues to the next. The result
    is a partial-but-useful context for the formatter.
    """
    ctx = IntrospectionContext()

    # 1. Legacy 4-var homeostasis
    try:
        from app.subia.homeostasis.state import get_state, get_behavioral_modifiers
        ctx.legacy_homeostasis = dict(get_state() or {})
        ctx.behavioural_modifiers = dict(get_behavioral_modifiers() or {})
        ctx.sources_ok.append("legacy_homeostasis")
    except Exception as exc:
        logger.debug("introspection: legacy gather failed: %s", exc)
        ctx.sources_failed.append("legacy_homeostasis")

    # 2. SubIA-native kernel via live_integration singleton
    try:
        from app.subia.live_integration import get_last_state
        live = get_last_state()
        kernel = getattr(live, "kernel", None) if live else None
        if kernel is not None:
            ctx.kernel_active = True
            ctx.kernel_homeostasis = dict(kernel.homeostasis.variables or {})
            ctx.kernel_set_points = dict(kernel.homeostasis.set_points or {})
            ctx.kernel_loop_count = int(getattr(kernel, "loop_count", 0))
            ctx.kernel_last_loop_at = str(getattr(kernel, "last_loop_at", "") or "")
            try:
                ctx.scene_focal = [
                    {
                        "id": getattr(it, "id", ""),
                        "summary": str(getattr(it, "summary", ""))[:80],
                        "salience": round(float(getattr(it, "salience", 0.0)), 3),
                        "ownership": getattr(it, "ownership", "self"),
                        "wonder_intensity": round(
                            float(getattr(it, "wonder_intensity", 0.0)), 3,
                        ),
                    }
                    for it in kernel.focal_scene()
                ]
                ctx.scene_peripheral_n = len(kernel.peripheral_scene())
            except Exception:
                pass

            tc = getattr(kernel, "temporal_context", None)
            if tc is not None:
                ctx.circadian_mode = getattr(tc, "circadian_mode", "")
                ctx.processing_density = round(
                    float(getattr(tc, "processing_density", 0.0) or 0.0), 3,
                )
            sp = getattr(kernel, "specious_present", None)
            if sp is not None:
                ctx.specious_present_direction = getattr(sp, "direction", "") or ""
                ctx.specious_present_tempo = round(
                    float(getattr(sp, "tempo", 0.0) or 0.0), 3,
                )

            # Phase 12 discovered_limitations + capabilities
            try:
                ctx.discovered_limitations = list(
                    getattr(kernel.self_state, "discovered_limitations", []) or []
                )[:10]
                caps = dict(getattr(kernel.self_state, "capabilities", {}) or {})
                ctx.discovered_capabilities = {
                    k: v for k, v in caps.items()
                    if isinstance(v, dict) and v.get("discovered")
                }
            except Exception:
                pass
            ctx.sources_ok.append("kernel")
        else:
            ctx.sources_failed.append("kernel:not_registered")
    except Exception as exc:
        logger.debug("introspection: kernel gather failed: %s", exc)
        ctx.sources_failed.append("kernel")

    # 3. Recent failures from error journal (last 24h)
    try:
        from app.error_handler import recent_errors
        errs = recent_errors(hours=24, limit=8) or []
        ctx.recent_failures = [
            {
                "kind": (e or {}).get("error_type", "unknown"),
                "context": str((e or {}).get("context", ""))[:120],
                "ts": (e or {}).get("timestamp", ""),
            }
            for e in errs
        ]
        ctx.sources_ok.append("error_journal")
    except Exception as exc:
        logger.debug("introspection: error journal gather failed: %s", exc)
        ctx.sources_failed.append("error_journal")

    # 4. System chronicle excerpt
    try:
        from pathlib import Path
        p = Path("/app/workspace/system_chronicle.md")
        if not p.exists():
            p = Path("workspace/system_chronicle.md")
        if p.exists():
            text = p.read_text(encoding="utf-8", errors="ignore")
            # Keep last ~600 chars — enough for "what's been happening"
            ctx.chronicle_excerpt = text[-800:].strip()
            ctx.sources_ok.append("chronicle")
    except Exception as exc:
        logger.debug("introspection: chronicle gather failed: %s", exc)
        ctx.sources_failed.append("chronicle")

    return ctx
