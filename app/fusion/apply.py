"""Fusion planning + injection — the bridge between config/panel/budget and
the single factory chokepoint.

``plan_for_role`` answers "should this completion be fused, and if so with what
panel?" and is the *only* function the factory calls. ``inject_plugin`` merges
the resulting plugin into the request's ``extra_body`` beside the provider-
routing config the factory already writes. ``fusion_state`` is the read-only
operator snapshot the REST/React surfaces render.
"""

from __future__ import annotations

from app.fusion import budget, config, panel

# Need ≥2 panel members for deliberation to be meaningful; below that we
# decline (a 1-model "panel" is just a slower single call at panel cost).
_MIN_PANEL = 2


def plan_for_role(
    role: str,
    *,
    max_tokens: int = 4096,
    cost_in: float = 0.0,
    cost_out: float = 0.0,
) -> tuple[dict | None, int]:
    """Decide whether to fuse *role*'s next completion.

    Returns ``(plugin, cost_factor)`` where ``plugin`` is the OpenRouter
    fusion plugin dict to attach (or ``None`` to run a normal single call)
    and ``cost_factor`` is ``panel_size + 1`` (panel calls + judge) — used by
    the factory to scale its per-call OpenRouter budget pre-check.

    Records the estimated fusion spend against the per-day cap when it decides
    to fuse. Fully failure-isolated: any error returns ``(None, 1)``.
    """
    try:
        if not config.is_enabled_for(role):
            return (None, 1)
        if config.brake_engaged():
            return (None, 1)
        cap = config.daily_cap_usd()
        if not budget.under_cap(cap):
            return (None, 1)

        members = panel.resolve_panel(
            classes=config.panel_classes(),
            pins=config.panel_pins(),
            hints=config.variant_hints(),
            max_panel=config.max_panel(),
            blocked=config.blocked_models(),
        )
        if len(members) < _MIN_PANEL:
            return (None, 1)

        plugin: dict = {"id": "fusion", "analysis_models": members}
        judge = panel.resolve_judge(config.judge_id())
        if judge:
            plugin["model"] = judge

        # Real fusion cost (the judge re-ingests every panel output) runs
        # ~10-20×, far above a naive panel_size+1 estimate — so we do NOT meter
        # an estimate here; ``observe.record_response`` records the ACTUAL
        # ``response_cost`` after the call. ``factor`` is only the rough
        # multiplier the factory applies to the separate OpenRouter pre-check.
        factor = len(members) + 1
        return (plugin, factor)
    except Exception:
        return (None, 1)


def agent_extra_body(role: str) -> dict | None:
    """Agent-path (CrewAI) fusion: returns ``{"plugins": [fusion_plugin]}`` to
    bake into the agent LLM's ``extra_body`` when agent-path fusion is enabled
    for *role*, else ``None``.

    Resolved at BUILD time (the role is known in ``create_specialist_llm``; the
    LLM is cached by model, not role, so the caller forks the cache key).
    **Offered, not forced** — carries no ``tool_choice`` — so it composes with
    the agent's own tools instead of blocking them. Deliberately NOT metered
    against the per-day cap (a cached LLM is reused and may not invoke fusion
    every call); the monthly-ceiling brake still gates it.
    """
    try:
        if not config.agent_path_enabled():
            return None
        if not config.is_enabled_for(role):
            return None
        if config.brake_engaged():
            return None
        members = panel.resolve_panel(
            classes=config.panel_classes(),
            pins=config.panel_pins(),
            hints=config.variant_hints(),
            max_panel=config.max_panel(),
            blocked=config.blocked_models(),
        )
        if len(members) < _MIN_PANEL:
            return None
        plugin: dict = {"id": "fusion", "analysis_models": members}
        judge = panel.resolve_judge(config.judge_id())
        if judge:
            plugin["model"] = judge
        return {"plugins": [plugin]}
    except Exception:
        return None


def inject_plugin(kwargs: dict, plugin: dict) -> None:
    """Merge *plugin* into ``kwargs['extra_body']['plugins']`` in place.

    Preserves any existing ``extra_body`` (notably the provider-routing
    ``ignore`` list the factory writes for Stealth-avoidance) — Fusion owns the
    ``plugins`` key, provider-exclusion owns the ``provider`` key, so they
    compose. Forces ``tool_choice="required"`` for deterministic deliberation
    (the operator turned fusion on for this role; don't let the outer model
    skip it), without clobbering a caller-supplied ``tool_choice``.
    """
    extra_body = dict(kwargs.get("extra_body") or {})
    plugins = list(extra_body.get("plugins") or [])
    plugins.append(plugin)
    extra_body["plugins"] = plugins
    kwargs["extra_body"] = extra_body
    kwargs.setdefault("tool_choice", "required")


def fusion_state() -> dict:
    """Read-only snapshot for the operator surfaces (REST + main-page chip).

    Resolves the panel server-side so the UI shows exactly which concrete
    models will run, not just the configured classes.
    """
    try:
        en = config.enabled()
        roles = config.scope_roles()
        classes = config.panel_classes()
        pins = config.panel_pins()
        hints = config.variant_hints()
        blocked = config.blocked_models()
        cap_n = config.max_panel()

        resolved: list[dict] = []
        for cls in classes[:cap_n]:
            pin = pins.get(cls)
            mid = (
                str(pin).strip()
                if pin
                else panel.champion_for_class(cls, hints.get(cls, ""), blocked)
            )
            resolved.append(
                {"class": cls, "model_id": mid, "pinned": bool(pin)}
            )

        members = [r["model_id"] for r in resolved if r["model_id"]]
        # de-dup preserving order
        seen: set[str] = set()
        members = [m for m in members if not (m in seen or seen.add(m))]

        try:
            from app.llm_catalog import PUBLIC_ROLES
            available_roles = list(PUBLIC_ROLES)
        except Exception:
            available_roles = []

        return {
            "enabled": en,
            "scope_roles": roles,
            "active": en and bool(roles) and len(members) >= _MIN_PANEL,
            "panel": resolved,
            "judge": config.judge_id() or "(OpenRouter default)",
            "max_panel": cap_n,
            "daily_cap_usd": config.daily_cap_usd(),
            "spent_today_usd": round(budget.spent_today(), 4),
            "brake_engaged": config.brake_engaged(),
            "cost_multiplier": (len(members) + 1) if members else 1,
            "available_roles": available_roles,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "enabled": False,
            "active": False,
            "scope_roles": [],
            "panel": [],
            "error": str(exc)[:200],
        }
