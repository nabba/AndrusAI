"""Fusion configuration reader.

Thin, defensive accessors over the ``fusion_*`` runtime-settings keys. Every
``app.runtime_settings`` import is lazy (inside the function) so this module
imports cleanly without the settings/pydantic stack — matching the dispatcher's
"don't import runtime_settings at module load" discipline and keeping the
package unit-testable on a bare host.

All accessors are failure-isolated: a read error falls back to the safe
(fusion-off / empty) value so a corrupt settings file can never *enable*
fusion or widen its blast radius.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator


_forced_roles: ContextVar[frozenset[str]] = ContextVar(
    "fusion_forced_roles", default=frozenset(),
)
_forced_panel_cap: ContextVar[int | None] = ContextVar(
    "fusion_forced_panel_cap", default=None,
)


@contextmanager
def force_for_roles(
    roles: set[str] | frozenset[str],
    *,
    max_panel: int = 3,
) -> Iterator[None]:
    """Request-scoped Fusion opt-in used by gated deep research.

    This does not weaken the cost brake or daily cap. It only bypasses the
    global role-scope switch for the named roles while the context is active.
    """
    role_token = _forced_roles.set(frozenset(str(r) for r in roles if r))
    cap_token = _forced_panel_cap.set(max(2, min(8, int(max_panel))))
    try:
        yield
    finally:
        _forced_panel_cap.reset(cap_token)
        _forced_roles.reset(role_token)


def is_forced_for(role: str) -> bool:
    """Whether this request explicitly selected a Fusion panel for ``role``."""
    return role in _forced_roles.get()


def effective_max_panel() -> int:
    """Apply the narrower request cap when a forced scope is active."""
    configured = max_panel()
    forced = _forced_panel_cap.get()
    return min(configured, forced) if forced is not None else configured


def enabled() -> bool:
    """Master switch. Default False."""
    try:
        from app.runtime_settings import get_fusion_enabled
        return bool(get_fusion_enabled())
    except Exception:
        return False


def scope_roles() -> list[str]:
    """Roles whose raw completions are fused. Empty by default."""
    try:
        from app.runtime_settings import get_fusion_scope_roles
        return list(get_fusion_scope_roles() or [])
    except Exception:
        return []


def is_enabled_for(role: str) -> bool:
    """True iff the master switch is ON and *role* is in scope."""
    return enabled() and (role in set(scope_roles()))


def agent_path_enabled() -> bool:
    """Whether fusion also applies to the CrewAI agent-LLM path (default OFF).

    A deliberate second opt-in beyond raw-path fusion: the agent path is
    offered-not-forced (composes with the agent's tools) and unmetered, so it
    sits behind its own switch on top of the master + scope gates.
    """
    try:
        from app.runtime_settings import get_fusion_agent_path_enabled
        return bool(get_fusion_agent_path_enabled())
    except Exception:
        return False


def panel_classes() -> list[str]:
    """Ordered vendor classes that make up the panel."""
    try:
        from app.runtime_settings import get_fusion_panel_classes
        return list(get_fusion_panel_classes() or [])
    except Exception:
        return []


def panel_pins() -> dict[str, str]:
    """Per-class explicit model-id overrides ({class: model_id})."""
    try:
        from app.runtime_settings import get_fusion_panel_pins
        return dict(get_fusion_panel_pins() or {})
    except Exception:
        return {}


def variant_hints() -> dict[str, str]:
    """Per-class slug-substring preference ({class: 'flash'})."""
    try:
        from app.runtime_settings import get_fusion_variant_hints
        return dict(get_fusion_variant_hints() or {})
    except Exception:
        return {}


def judge_id() -> str:
    """Explicit judge model id, or '' to let OpenRouter pick its default."""
    try:
        from app.runtime_settings import get_fusion_judge_id
        return str(get_fusion_judge_id() or "")
    except Exception:
        return ""


def max_panel() -> int:
    """Hard cap on panel size (OpenRouter allows 1–8). Default 4."""
    try:
        from app.runtime_settings import get_fusion_max_panel
        return int(get_fusion_max_panel() or 4)
    except Exception:
        return 4


def daily_cap_usd() -> float:
    """Per-day fusion spend cap in USD. 0 ⇒ no fusion-specific cap."""
    try:
        from app.runtime_settings import get_fusion_daily_cap_usd
        return float(get_fusion_daily_cap_usd() or 0.0)
    except Exception:
        return 0.0


def blocked_models() -> set[str]:
    """Model ids/keys the operator has blocked (reused from self-heal)."""
    try:
        from app.runtime_settings import get_chat_blocked_models
        return set(get_chat_blocked_models() or [])
    except Exception:
        return set()


def brake_engaged() -> bool:
    """True when the monthly total-cost-ceiling brake is engaged.

    When the brake is on, idle MEDIUM/HEAVY work is already paused; fusion
    (a 4–5× cost multiplier) backs off the same way and falls back to
    single-model completions.
    """
    try:
        from app.runtime_settings import snapshot
        return bool(snapshot().get("idle_pause_due_to_budget", False))
    except Exception:
        return False
