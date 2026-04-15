"""
subia.live_integration — feature-flagged wire-in of SubIA CIL hooks
into the existing app.lifecycle_hooks registry.

Behaviour:

  enable_subia_hooks(feature_flag, hook_registry=None, cache=None,
                     llm=None) -> bool

If the feature flag is false, do nothing and return False.
If true, construct a SubIALoop with:
  - A shared SubjectivityKernel (loaded from wiki/self/kernel-state.md
    if present, otherwise a fresh default kernel).
  - A fresh CompetitiveGate as the scene bottleneck.
  - A cached, LLM-backed predict_fn via cache.cached_predict_fn +
    llm_predict.build_llm_predict_fn.
  - app/subia/belief/dispatch_gate.decide_dispatch as the decider.

Then register two hooks with the existing registry:

  subia_pre_task   at HookPoint.PRE_TASK,     priority=25
  subia_post_task  at HookPoint.ON_COMPLETE,  priority=25

Priorities sit after the existing immutable safety hooks (priority 0-5)
so SubIA never runs before the DGM gates fire.

Errors during hook execution go through the CIL's own containment
(every step is wrapped) and never crash the host task. If registration
itself fails for structural reasons (wrong registry shape), this
function returns False and logs — the host process continues without
SubIA rather than refusing to boot.

Off by default. Callers opt in by setting SUBIA_FEATURE_FLAG_LIVE=1
or the equivalent settings field.

See PROGRAM.md Phase 4.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

from app.subia.kernel import SubjectivityKernel

logger = logging.getLogger(__name__)


# Environment-variable fallback, in case no explicit flag is passed.
_ENV_FLAG = "SUBIA_FEATURE_FLAG_LIVE"

# Process-local singleton exposing the most recent integration state.
# Populated by enable_subia_hooks(); read by diagnostic surfaces
# (firebase.publish.report_subia_state and similar).
_last_state: "LiveIntegrationState | None" = None


def get_last_state() -> "LiveIntegrationState | None":
    """Return the most recent LiveIntegrationState, or None if
    enable_subia_hooks() has never been called successfully."""
    return _last_state


@dataclass
class LiveIntegrationState:
    """Holds references to the live integration objects. Returned by
    enable_subia_hooks() so tests and ops can introspect.
    """
    kernel: Optional[SubjectivityKernel] = None
    loop: Any = None
    hooks: Any = None
    registered: bool = False
    reason: str = ""


def enable_subia_hooks(
    feature_flag: bool | None = None,
    *,
    hook_registry: Any = None,
    cache: Any = None,
    llm: Any = None,
    load_kernel: bool = True,
) -> LiveIntegrationState:
    """Opt-in registration of SubIA hooks into the live lifecycle registry.

    Args:
        feature_flag:   True to actually register. None → read
                        SUBIA_FEATURE_FLAG_LIVE env var.
        hook_registry:  Registry to register with. None → use
                        app.lifecycle_hooks.get_registry().
        cache:          Optional PredictionCache. Fresh one created if None.
        llm:            Optional pre-built LLM. When None, the predict
                        function lazy-constructs via llm_factory on
                        first call.
        load_kernel:    If True, try to load kernel-state.md from disk;
                        fallback to fresh kernel.

    Returns a LiveIntegrationState describing what happened. Never
    raises.
    """
    if feature_flag is None:
        feature_flag = _resolve_env_flag()

    global _last_state
    state = LiveIntegrationState()
    if not feature_flag:
        state.reason = "feature_flag disabled"
        logger.info("subia.live_integration: skipped (flag off)")
        _last_state = state
        return state

    try:
        # Lazy imports so this module has no import-time cost when the
        # flag is off.
        from app.subia.belief.dispatch_gate import decide_dispatch
        from app.subia.loop import SubIALoop
        from app.subia.hooks import SubIALifecycleHooks
        from app.subia.prediction.cache import (
            PredictionCache,
            cached_predict_fn,
        )
        from app.subia.prediction.llm_predict import build_llm_predict_fn
        from app.subia.scene.buffer import CompetitiveGate
    except Exception as exc:
        state.reason = f"import failed: {exc}"
        logger.exception("subia.live_integration: import failure")
        return state

    # Kernel: try to load from disk if caller requested it.
    try:
        if load_kernel:
            from app.subia.persistence import load_kernel_state
            state.kernel = load_kernel_state()
        else:
            state.kernel = SubjectivityKernel()
    except Exception:
        logger.exception("subia.live_integration: kernel load failed")
        state.kernel = SubjectivityKernel()

    # Predict: cached wrapper around the live LLM predictor.
    live_predict = build_llm_predict_fn(llm=llm)
    cache = cache or PredictionCache()
    predict_fn = cached_predict_fn(live_predict, cache)

    # Loop and hooks.
    gate = CompetitiveGate(capacity=5)
    loop = SubIALoop(
        kernel=state.kernel,
        scene_gate=gate,
        predict_fn=predict_fn,
        consult_fn=None,        # wired in Phase 8 w/ real belief store
        dispatch_decider=decide_dispatch,
    )
    state.loop = loop
    state.hooks = SubIALifecycleHooks(loop=loop)

    # Register with the live lifecycle registry.
    try:
        registry = hook_registry or _get_live_registry()
        if registry is None:
            state.reason = "live registry unavailable"
            return state
        _register_subia(registry, state.hooks)
        state.registered = True
        state.reason = "registered"
        logger.info("subia.live_integration: hooks registered with live registry")
    except Exception as exc:
        state.reason = f"registration failed: {exc}"
        logger.exception("subia.live_integration: registration failure")

    _last_state = state
    return state


# ── Internals ─────────────────────────────────────────────────────

def _resolve_env_flag() -> bool:
    raw = os.environ.get(_ENV_FLAG, "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _get_live_registry():
    try:
        from app.lifecycle_hooks import get_registry
    except Exception:
        logger.exception("subia.live_integration: lifecycle_hooks unavailable")
        return None
    try:
        return get_registry()
    except Exception:
        logger.exception("subia.live_integration: get_registry failed")
        return None


def _register_subia(registry: Any, subia_hooks: Any) -> None:
    """Register SubIA pre/post hooks with the registry. Compatible with
    app.lifecycle_hooks.HookRegistry shape (ctx in, ctx out) by
    wrapping our string/dict hooks.
    """
    from app.lifecycle_hooks import HookPoint

    pre_fn = _wrap_pre(subia_hooks)
    post_fn = _wrap_post(subia_hooks)

    # Idempotent: unregister any prior copy first.
    for name, hp in (
        ("subia_pre_task", HookPoint.PRE_TASK),
        ("subia_post_task", HookPoint.ON_COMPLETE),
    ):
        try:
            registry.unregister(name, hp)
        except Exception:
            pass

    registry.register(
        name="subia_pre_task",
        hook_point=HookPoint.PRE_TASK,
        fn=pre_fn,
        priority=25,
        immutable=False,
        description="SubIA CIL pre-task sequencer (Phase 4)",
    )
    registry.register(
        name="subia_post_task",
        hook_point=HookPoint.ON_COMPLETE,
        fn=post_fn,
        priority=25,
        immutable=False,
        description="SubIA CIL post-task sequencer (Phase 4)",
    )


def _wrap_pre(subia_hooks):
    """Adapter: HookContext → SubIALifecycleHooks.pre_task → HookContext."""
    def _pre(ctx):
        try:
            # Build lightweight agent/task stand-ins from the HookContext
            agent = _AgentShim(ctx.agent_id)
            task = _TaskShim(ctx.task_description)
            injection = subia_hooks.pre_task(agent, task)
            ctx.set("subia_context_injection", injection)
        except Exception:
            logger.exception("subia pre-task wrapper failed")
        return ctx
    return _pre


def _wrap_post(subia_hooks):
    def _post(ctx):
        try:
            agent = _AgentShim(ctx.agent_id)
            task = _TaskShim(ctx.task_description)
            # task_result: prefer modified_data["result"], fall back to data.
            result = ctx.get("result", None)
            subia_hooks.post_task(agent, task, result)
        except Exception:
            logger.exception("subia post-task wrapper failed")
        return ctx
    return _post


@dataclass
class _AgentShim:
    role: str


@dataclass
class _TaskShim:
    description: str
