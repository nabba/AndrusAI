"""
subia.live_integration — feature-flagged wire-in of SubIA CIL hooks
into the existing app.lifecycle_hooks registry.

Behaviour:

  enable_subia_hooks(feature_flag, hook_registry=None, cache=None,
                     llm=None) -> LiveIntegrationState

If the feature flag is false, do nothing and return an inactive state.
If true, construct a SubIALoop wired with:
  - A shared SubjectivityKernel (loaded from wiki/self/kernel-state.md
    if present, otherwise a fresh default kernel). The kernel is also
    published via app.subia.kernel.set_active_kernel() so downstream
    read-only consumers (evolution, shinka_engine, confidence_tracker,
    …) can observe current homeostatic state.
  - A fresh CompetitiveGate as the scene bottleneck.
  - The singleton PredictiveLayer with the gate attached (Phase 2 PP-1
    surprise routing — Step 8 compare now has a real predictive_layer).
  - A cached, LLM-backed predict_fn via cache.cached_predict_fn +
    llm_predict.build_llm_predict_fn, enriched with temporal + technical
    context at prompt-build time.
  - app/subia/belief/dispatch_gate.decide_dispatch as the decider.
  - A consult_fn backed by the PostgreSQL belief store, so Step 6
    (Monitor) actually reads ACTIVE / SUSPENDED beliefs and the HOT-3
    closure fires against real data instead of defaulting to ALLOW.

Then register two hooks with the existing registry:

  subia_pre_task   at HookPoint.PRE_TASK,     priority=25
  subia_post_task  at HookPoint.ON_COMPLETE,  priority=25

The same hook pair is also bound to the legacy crew-boundary extension
points in app.crews.lifecycle (subia_pre_task / subia_post_task) so
crews that run outside the orchestrator (e.g. retrospective crews
triggered autonomously) still get a SubIA pass. The crew-boundary pass
runs as the compressed loop (operation_type='crew_kickoff') to avoid
duplicating the expensive predict+cascade work already done at the
orchestrator boundary.

Priorities sit after the existing immutable safety hooks (priority 0-5)
so SubIA never runs before the DGM gates fire.

Errors during hook execution go through the CIL's own containment
(every step is wrapped) and never crash the host task. If registration
itself fails for structural reasons (wrong registry shape), this
function returns an inactive state and logs — the host process
continues without SubIA rather than refusing to boot.

Off by default. Callers opt in by setting SUBIA_FEATURE_FLAG_LIVE=1
or the equivalent settings field.

See PROGRAM.md Phase 4 / Phase 16a.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

from app.subia.kernel import SubjectivityKernel, set_active_kernel

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
    gate: Any = None
    predictive_layer: Any = None
    pds_bridge: Any = None
    registered: bool = False
    reason: str = ""


def get_live_predictive_layer() -> Any:
    """Return the PredictiveLayer bound to the active SubIALoop.

    Used by external predict→compare clients (Firecrawl wrapper,
    inbound-signal routers) that want to route their own content
    through SubIA's PP-1 surprise path. Returns None when the
    feature flag is off or the last boot attempt failed.
    """
    state = _last_state
    return state.predictive_layer if state else None


def get_live_pds_bridge() -> Any:
    """Return the PDSBridge bound to the active SubIALoop.

    External consumers (Shadow findings, observer outcomes, explicit
    user feedback) push PDS nudges through this instance via
    `apply_nudge(parameter, delta, reason=...)`. The bridge enforces
    per-loop / per-week caps regardless of caller. Returns None when
    SubIA is not active.
    """
    state = _last_state
    return state.pds_bridge if state else None


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
        from app.subia.prediction.layer import get_predictive_layer
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

    # Publish the kernel for read-only observation by downstream
    # subsystems (evolution, shinka_engine, confidence_tracker, …).
    set_active_kernel(state.kernel)

    # Predict: cached wrapper around the live LLM predictor, enriched
    # with the available temporal / technical context.
    live_predict = build_llm_predict_fn(llm=llm)
    enriched_predict = _wrap_with_context_enrichment(live_predict, state.kernel)
    cache = cache or PredictionCache()
    predict_fn = cached_predict_fn(enriched_predict, cache)

    # Scene bottleneck + PP-1 predictive layer. The layer's gate binding
    # is the Phase 2 surprise-routing closure — attaching it here means
    # Step 8 compare routes surprise back into the scene on live traffic.
    gate = CompetitiveGate(capacity=5)
    predictive_layer = get_predictive_layer()
    try:
        predictive_layer.set_gate(gate)
    except AttributeError:
        pass

    # consult_fn backed by the PostgreSQL belief store. decide_dispatch
    # receives real beliefs so the HOT-3 closure gates actual dispatch
    # decisions rather than always allowing.
    consult_fn = _build_belief_consult_fn()

    # PDS bridge: bounded-write edge into personality-development-state.
    # Built in dry-run (no pds client) so external consumers can push
    # evidence via apply_nudge() and the caps / audit trail apply even
    # when the PDS subsystem isn't connected — the usage ledger is
    # inspectable via bridge.to_dict().
    try:
        from app.subia.connections.pds_bridge import PDSBridge
        state.pds_bridge = PDSBridge()
    except Exception:
        logger.debug("subia.live_integration: PDSBridge unavailable",
                     exc_info=True)

    loop = SubIALoop(
        kernel=state.kernel,
        scene_gate=gate,
        predict_fn=predict_fn,
        predictive_layer=predictive_layer,
        consult_fn=consult_fn,
        dispatch_decider=decide_dispatch,
    )
    state.loop = loop
    state.gate = gate
    state.predictive_layer = predictive_layer
    state.hooks = SubIALifecycleHooks(loop=loop)

    # Bind the legacy crew-boundary extension points so crews that run
    # outside the orchestrator also get a SubIA pass (compressed loop).
    _bind_crew_lifecycle_stubs(state.hooks)

    # Register with the live lifecycle registry.
    try:
        registry = hook_registry or _get_live_registry()
        if registry is None:
            state.reason = "live registry unavailable"
            _last_state = state
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


# ── consult_fn, predictor enrichment, crew-lifecycle binding ─────

def _build_belief_consult_fn():
    """Return a consult_fn that queries the PostgreSQL belief store.

    Step 6 (Monitor) passes the resulting list into decide_dispatch,
    which distinguishes ACTIVE from SUSPENDED/RETRACTED and reads
    confidence. When the belief store is unreachable (database down,
    schema missing in dev) the function returns an empty list so the
    dispatch gate ESCALATEs rather than fabricating beliefs.
    """
    def _consult(*, task_description: str, crew_name: str,
                 goal_context: str = "") -> list:
        query = (task_description or "")[:500]
        if goal_context:
            query = (query + " " + goal_context)[:500]
        try:
            from app.subia.belief.store import get_belief_store
            store = get_belief_store()
        except Exception:
            logger.debug("subia.live_integration: belief store unavailable",
                         exc_info=True)
            return []
        try:
            return list(store.query_relevant(query, n=5, min_confidence=0.0))
        except Exception:
            logger.debug("subia.live_integration: belief query failed",
                         exc_info=True)
            return []
    return _consult


def _wrap_with_context_enrichment(base_predict_fn, kernel: SubjectivityKernel):
    """Wrap the live LLM predict_fn so Step 5 prompts are enriched with
    the temporal + technical context bridges before the LLM sees them.

    The bridges (app.subia.connections.temporal_subia_bridge,
    tsal_subia_bridge) document themselves as "closed-loop" — they are
    closed at this edge: circadian mode, subjective time, compute
    pressure, and cascade-tier availability reach the predictor.
    """
    def _enriched(ctx: dict):
        # The underlying build_llm_predict_fn assembles its prompt from
        # the context dict; we pre-seed an `extra_prompt_context` entry
        # that llm_predict concatenates before calling the LLM.
        enriched_ctx = dict(ctx)
        extras: list[str] = []
        try:
            from app.subia.connections.temporal_subia_bridge import (
                enrich_prediction_with_temporal_context,
            )
            suffix = enrich_prediction_with_temporal_context("", kernel)
            if suffix.strip():
                extras.append(suffix.strip())
        except Exception:
            logger.debug("temporal prompt enrichment failed", exc_info=True)
        try:
            from app.subia.connections.tsal_subia_bridge import (
                enrich_prediction_with_technical_context,
            )
            from app.subia.tsal.refresh import get_last_model
            suffix = enrich_prediction_with_technical_context("", get_last_model())
            if suffix.strip():
                extras.append(suffix.strip())
        except Exception:
            logger.debug("tsal prompt enrichment failed", exc_info=True)
        if extras:
            enriched_ctx["extra_prompt_context"] = "\n".join(extras)
        return base_predict_fn(enriched_ctx)
    return _enriched


def _bind_crew_lifecycle_stubs(hooks: Any) -> None:
    """Reassign the app.crews.lifecycle SubIA extension points.

    The context manager in app/crews/lifecycle.py calls two module-level
    callables (subia_pre_task, subia_post_task) at crew enter/exit. They
    start as no-ops; we bind them here to a compressed SubIA pass so
    direct-run crews (retrospective, idle, cron-triggered) also feed the
    kernel without duplicating the full-loop work already done at the
    orchestrator boundary.
    """
    try:
        import app.crews.lifecycle as _cl
    except Exception:
        logger.debug("subia.live_integration: crews.lifecycle unavailable",
                     exc_info=True)
        return

    def _pre(crew_name: str, task_title: str) -> None:
        try:
            hooks.pre_task(_AgentShim(role=str(crew_name or "crew")),
                           _CrewTaskShim(description=str(task_title or ""),
                                          operation_type="crew_kickoff"))
        except Exception:
            logger.debug("crew_lifecycle subia_pre_task failed", exc_info=True)

    def _post(crew_name: str, status: str, exc: Exception | None) -> None:
        try:
            result = {
                "success": status == "success",
                "summary": status or "",
                "error": repr(exc) if exc else "",
            }
            hooks.post_task(_AgentShim(role=str(crew_name or "crew")),
                            _CrewTaskShim(description="crew_kickoff",
                                           operation_type="crew_kickoff"),
                            result)
        except Exception:
            logger.debug("crew_lifecycle subia_post_task failed", exc_info=True)

    _cl.subia_pre_task = _pre
    _cl.subia_post_task = _post


@dataclass
class _CrewTaskShim:
    """Task-shaped shim that lets the hooks classify the operation
    as `crew_kickoff` (compressed loop) via its description field.

    The existing SubIALifecycleHooks._classify_operation heuristic reads
    the description; embedding `crew_kickoff` in the description is the
    cleanest way to steer classification without plumbing a new kwarg.
    """
    description: str
    operation_type: str = "crew_kickoff"
