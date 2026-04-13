"""
subia.loop — the 11-step Consciousness Integration Loop (CIL).

The loop sequences the five Phase-2-closed gates into a single
pre-task / post-task pair. Per Amendment B, only Step 5 (Predict)
requires an LLM call on the hot path; every other step is
deterministic arithmetic over existing kernel state.

              PRE-TASK                            POST-TASK
    ┌────────────────────────┐            ┌────────────────────────┐
    │ 1  Perceive            │            │ 7  Act (task runs)     │
    │ 2  Feel (homeostasis)  │            │ 8  Compare (PE error)  │
    │ 3  Attend (scene gate) │            │ 9  Update (state)      │
    │ 4  Own (self-state)    │            │ 10 Consolidate (memory)│
    │ 5  Predict (LLM tier1) │            │ 11 Reflect (audit)     │
    │ 5b Cascade modulation  │            └────────────────────────┘
    │ 6  Monitor             │
    └────────────────────────┘

Operation classification (from SUBIA_CONFIG):
  - FULL_LOOP_OPERATIONS    run all 11 steps
  - COMPRESSED_LOOP_OPS     run steps 1-3, 7-9 only

The loop is pure orchestration. It does not:
  - Call external databases directly (gates handle persistence)
  - Perform LLM calls except through the injected predict_fn
  - Mutate global state (all changes flow through gates)

Failure containment: a step that raises is logged and the loop
continues. A crashed step must never break the agent task.

Infrastructure-level. Not agent-modifiable. See PROGRAM.md Phase 4.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from app.subia.config import SUBIA_CONFIG
from app.subia.kernel import (
    Prediction,
    SceneItem,
    SubjectivityKernel,
)

logger = logging.getLogger(__name__)


# ── Result types ───────────────────────────────────────────────────

@dataclass
class StepOutcome:
    """Record of a single step's execution."""
    step: str
    ok: bool = True
    elapsed_ms: float = 0.0
    error: Optional[str] = None
    details: dict = field(default_factory=dict)


@dataclass
class CILResult:
    """Aggregate result of a full or compressed loop invocation."""
    loop_type: str                  # "full" | "compressed"
    phase: str                      # "pre_task" | "post_task"
    steps: list = field(default_factory=list)    # List[StepOutcome]
    total_elapsed_ms: float = 0.0
    context_for_agent: dict = field(default_factory=dict)
    within_budget: bool = True
    budget_ms: float = 0.0

    def add(self, outcome: StepOutcome) -> None:
        self.steps.append(outcome)
        self.total_elapsed_ms += outcome.elapsed_ms

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.steps)

    def step(self, name: str) -> Optional[StepOutcome]:
        for s in self.steps:
            if s.step == name:
                return s
        return None

    def to_dict(self) -> dict:
        return {
            "loop_type": self.loop_type,
            "phase": self.phase,
            "ok": self.ok,
            "total_elapsed_ms": round(self.total_elapsed_ms, 2),
            "budget_ms": self.budget_ms,
            "within_budget": self.within_budget,
            "steps": [
                {
                    "step": s.step,
                    "ok": s.ok,
                    "elapsed_ms": round(s.elapsed_ms, 2),
                    "error": s.error,
                    "details": s.details,
                }
                for s in self.steps
            ],
        }


# ── Operation classification ───────────────────────────────────────

def classify_operation(operation_type: str) -> str:
    """Return 'full' or 'compressed' based on SUBIA_CONFIG."""
    if operation_type in SUBIA_CONFIG.get("FULL_LOOP_OPERATIONS", ()):
        return "full"
    if operation_type in SUBIA_CONFIG.get("COMPRESSED_LOOP_OPERATIONS", ()):
        return "compressed"
    # Unknown operations default to compressed — be cheap by default.
    return "compressed"


# ── Loop implementation ────────────────────────────────────────────

class SubIALoop:
    """Orchestrator for the 11-step Consciousness Integration Loop.

    Dependencies are injected at construction so the loop is testable
    in complete isolation. Production wiring (Phase 4 lifecycle hook
    integration) will build an instance with real gates; tests pass
    doubles.

    Args:
        kernel:          SubjectivityKernel to read/write.
        scene_gate:      CompetitiveGate (for Step 3 admissions).
        predict_fn:      Callable[[dict], Prediction] — step 5 predictor.
                         Dict keys: agent_role, task_description, scene,
                         self_state, homeostasis, history_window.
                         Returns a Prediction. May call an LLM; the loop
                         does not care.
        predictive_layer: optional PredictiveLayer for step 8 error
                         computation + surprise routing.
        hierarchy:       optional PredictionHierarchy for Step 5 injection
                         string.
        consult_fn:      Callable[[str, str, str], list] returning
                         consulted beliefs for Step 6. Dict keys:
                         task_description, crew_name, goal_context.
        dispatch_decider: optional DispatchDecider callable returning a
                         DispatchDecision — defaults to the
                         subia.belief.dispatch_gate module function.
        hedger:          optional callable for Step 11 response hedging
                         (only used if the orchestrator feeds outputs
                         through here on post-task).
        now:             clock for testability (default: time.monotonic).
    """

    def __init__(
        self,
        kernel: SubjectivityKernel,
        scene_gate: Any | None = None,
        predict_fn: Callable[[dict], Prediction] | None = None,
        predictive_layer: Any | None = None,
        hierarchy: Any | None = None,
        consult_fn: Callable[..., list] | None = None,
        dispatch_decider: Callable[..., Any] | None = None,
        hedger: Callable[..., tuple] | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.kernel = kernel
        self._gate = scene_gate
        self._predict_fn = predict_fn
        self._predictive_layer = predictive_layer
        self._hierarchy = hierarchy
        self._consult_fn = consult_fn
        self._dispatch_decider = dispatch_decider
        self._hedger = hedger
        self._now = now

        # Attach gate to predictive_layer so PP-1 routing fires.
        if self._predictive_layer is not None and self._gate is not None:
            try:
                self._predictive_layer.set_gate(self._gate)
            except AttributeError:
                pass

    # ── Public API ────────────────────────────────────────────────

    def pre_task(
        self,
        *,
        agent_role: str,
        task_description: str,
        operation_type: str = "task_execute",
        input_items: Iterable[SceneItem] = (),
        goal_context: str = "",
    ) -> CILResult:
        """Run the pre-task half of the CIL. Returns a CILResult with
        aggregated step outcomes and an injectable `context_for_agent`
        dict the caller hands to the agent.
        """
        loop_type = classify_operation(operation_type)
        budget_ms = float(
            SUBIA_CONFIG["FULL_LOOP_LATENCY_BUDGET_MS"]
            if loop_type == "full"
            else SUBIA_CONFIG["COMPRESSED_LOOP_LATENCY_BUDGET_MS"]
        )
        result = CILResult(
            loop_type=loop_type, phase="pre_task", budget_ms=budget_ms,
        )
        t_start = self._now()

        # Step 1: PERCEIVE (deterministic)
        self._run(result, "1_perceive", lambda: self._step_perceive(input_items))

        # Step 2: FEEL (deterministic — arithmetic on homeostasis)
        self._run(result, "2_feel", self._step_feel)

        # Step 3: ATTEND (deterministic — scene gate admissions)
        self._run(result, "3_attend", self._step_attend)

        if loop_type == "compressed":
            result.context_for_agent = self._build_compressed_context()
            result.total_elapsed_ms = (self._now() - t_start) * 1000.0
            result.within_budget = result.total_elapsed_ms <= budget_ms
            return result

        # Step 4: OWN (deterministic — ownership tagging)
        self._run(result, "4_own", self._step_own)

        # Step 5: PREDICT (LLM tier 1, the one allowed hot-path call)
        self._run(result, "5_predict",
                  lambda: self._step_predict(agent_role, task_description))

        # Step 5b: Cascade modulation (deterministic)
        self._run(result, "5b_cascade", self._step_cascade)

        # Step 6: MONITOR + belief-gated dispatch decision
        self._run(result, "6_monitor",
                  lambda: self._step_monitor(
                      task_description=task_description,
                      crew_name=agent_role,
                      goal_context=goal_context,
                  ))

        result.context_for_agent = self._build_full_context()
        result.total_elapsed_ms = (self._now() - t_start) * 1000.0
        result.within_budget = result.total_elapsed_ms <= budget_ms
        return result

    def post_task(
        self,
        *,
        agent_role: str,
        task_description: str,
        operation_type: str = "task_execute",
        task_result: dict | None = None,
        actual_content: str = "",
        actual_embedding: list[float] | None = None,
    ) -> CILResult:
        """Run the post-task half of the CIL."""
        loop_type = classify_operation(operation_type)
        budget_ms = float(
            SUBIA_CONFIG["FULL_LOOP_LATENCY_BUDGET_MS"]
            if loop_type == "full"
            else SUBIA_CONFIG["COMPRESSED_LOOP_LATENCY_BUDGET_MS"]
        )
        result = CILResult(
            loop_type=loop_type, phase="post_task", budget_ms=budget_ms,
        )
        t_start = self._now()
        task_result = task_result or {}

        # Step 8: COMPARE (prediction error, may route via PP-1)
        self._run(result, "8_compare",
                  lambda: self._step_compare(
                      agent_role=agent_role,
                      task_description=task_description,
                      actual_content=actual_content,
                      actual_embedding=actual_embedding,
                  ))

        # Step 9: UPDATE (deterministic kernel state updates)
        self._run(result, "9_update",
                  lambda: self._step_update(task_result))

        if loop_type == "compressed":
            result.total_elapsed_ms = (self._now() - t_start) * 1000.0
            result.within_budget = result.total_elapsed_ms <= budget_ms
            self.kernel.loop_count += 1
            self.kernel.touch()
            return result

        # Step 10: CONSOLIDATE (stub — implemented in Phase 7)
        self._run(result, "10_consolidate",
                  lambda: self._step_consolidate(task_result))

        # Step 11: REFLECT (periodic narrative audit — placeholder)
        self._run(result, "11_reflect", self._step_reflect)

        # Advance kernel cycle
        self.kernel.loop_count += 1
        self.kernel.touch()

        # Advance gate cycles when available
        for obj, attr in (
            (self._gate, "advance_cycle"),
            (self._predictive_layer, "advance_cycle"),
        ):
            fn = getattr(obj, attr, None) if obj is not None else None
            if callable(fn):
                try:
                    fn()
                except Exception:
                    logger.debug("advance_cycle raised", exc_info=True)

        result.total_elapsed_ms = (self._now() - t_start) * 1000.0
        result.within_budget = result.total_elapsed_ms <= budget_ms
        return result

    # ── Step implementations ──────────────────────────────────────

    def _step_perceive(self, input_items: Iterable[SceneItem]) -> dict:
        """Step 1: ingest candidates into the kernel's transient buffer."""
        count = 0
        self._candidates = []
        for item in input_items:
            self._candidates.append(item)
            count += 1
        return {"candidates": count}

    def _step_feel(self) -> dict:
        """Step 2: homeostasis update (deterministic stub).

        Full homeostatic arithmetic lives in subia.homeostasis.state.
        We do not call it here to keep the loop independent of the
        homeostasis backend until Phase 7/8 wires it in. For now,
        touch last_updated to mark activity.
        """
        h = self.kernel.homeostasis
        from datetime import datetime, timezone
        h.last_updated = datetime.now(timezone.utc).isoformat()
        return {
            "variables": len(h.variables),
            "deviations_above_threshold": sum(
                1 for d in h.deviations.values()
                if abs(d) > SUBIA_CONFIG["HOMEOSTATIC_DEVIATION_THRESHOLD"]
            ),
        }

    def _step_attend(self) -> dict:
        """Step 3: submit candidates to the scene gate (admits/rejects)."""
        if self._gate is None:
            return {"gate": "not_attached"}
        admitted = 0
        rejected = 0
        for candidate in getattr(self, "_candidates", []):
            # Translate SceneItem to the buffer's WorkspaceItem shape
            # lazily; if mismatched types, skip.
            try:
                result = self._gate.evaluate(candidate)
                if getattr(result, "admitted", False):
                    admitted += 1
                else:
                    rejected += 1
            except Exception:
                logger.debug("scene gate evaluate failed", exc_info=True)
        return {"admitted": admitted, "rejected": rejected}

    def _step_own(self) -> dict:
        """Step 4: tag admitted items with ownership — deterministic."""
        # Kernel scene items already carry an `ownership` field; without
        # a specific policy to override, default everything to 'self'.
        tagged = 0
        for item in self.kernel.scene:
            if not getattr(item, "ownership", None):
                item.ownership = "self"
                tagged += 1
        return {"newly_tagged": tagged, "total_items": len(self.kernel.scene)}

    def _step_predict(self, agent_role: str, task_description: str) -> dict:
        """Step 5: counterfactual prediction (LLM tier 1 if predict_fn)."""
        if self._predict_fn is None:
            return {"predict_fn": "not_attached"}
        prediction = self._predict_fn({
            "agent_role": agent_role,
            "task_description": task_description,
            "scene": list(self.kernel.scene),
            "self_state": self.kernel.self_state,
            "homeostasis": self.kernel.homeostasis,
            "prediction_history": list(self.kernel.predictions)[
                -SUBIA_CONFIG["PREDICTION_HISTORY_WINDOW"]:
            ],
        })
        self.kernel.predictions.append(prediction)
        return {
            "prediction_id": getattr(prediction, "id", ""),
            "confidence": getattr(prediction, "confidence", 0.5),
        }

    def _step_cascade(self) -> dict:
        """Step 5b: cascade tier modulation — deterministic recommendation."""
        last_pred = self.kernel.predictions[-1] if self.kernel.predictions else None
        confidence = getattr(last_pred, "confidence", 0.5) if last_pred else 0.5
        threshold = float(SUBIA_CONFIG["CASCADE_CONFIDENCE_THRESHOLD"])
        premium = float(SUBIA_CONFIG["CASCADE_PREMIUM_CONFIDENCE_FLOOR"])
        if confidence < premium:
            recommendation = "escalate_premium"
        elif confidence < threshold:
            recommendation = "escalate"
        else:
            recommendation = "maintain"
        self._cascade_recommendation = recommendation
        return {"recommendation": recommendation, "confidence": confidence}

    def _step_monitor(
        self,
        *,
        task_description: str,
        crew_name: str,
        goal_context: str,
    ) -> dict:
        """Step 6: monitor + belief-gated dispatch decision.

        Uses the Phase-2-closed HOT-3 dispatch_gate. Beliefs come from
        the injected consult_fn; if no consult_fn is wired, we skip
        the gate and ALLOW by default (loop continues functioning
        even when the belief subsystem is inert).
        """
        if self._consult_fn is None:
            self._dispatch_decision = None
            return {"dispatch": "no_consult_fn"}

        try:
            beliefs = list(self._consult_fn(
                task_description=task_description,
                crew_name=crew_name,
                goal_context=goal_context,
            ))
        except Exception:
            logger.debug("consult_fn raised", exc_info=True)
            beliefs = []

        decider = self._dispatch_decider
        if decider is None:
            from app.subia.belief.dispatch_gate import decide_dispatch
            decider = decide_dispatch

        decision = decider(
            consulted_beliefs=beliefs,
            suspended_candidates=(),  # Wired in Phase 8 with real DB query
            task_description=task_description,
            crew_name=crew_name,
        )
        self._dispatch_decision = decision
        return {
            "verdict": getattr(decision, "verdict", "ALLOW"),
            "belief_count": getattr(decision, "belief_count", 0),
        }

    def _step_compare(
        self,
        *,
        agent_role: str,
        task_description: str,
        actual_content: str,
        actual_embedding: list[float] | None,
    ) -> dict:
        """Step 8: prediction-error computation. PP-1 routing fires
        automatically if the predictive_layer has a gate attached
        (set in __init__).
        """
        if self._predictive_layer is None:
            return {"predictive_layer": "not_attached"}
        try:
            error = self._predictive_layer.predict_and_compare(
                channel=agent_role,
                context=task_description,
                actual_content=actual_content,
                actual_embedding=actual_embedding,
            )
        except Exception:
            logger.debug("predictive_layer.predict_and_compare failed",
                         exc_info=True)
            return {"error": "predict_and_compare_failed"}

        return {
            "error_magnitude": getattr(error, "error_magnitude", 0.0),
            "surprise_level": getattr(error, "surprise_level", "EXPECTED"),
            "routed_to_workspace": getattr(error, "routed_to_workspace", False),
        }

    def _step_update(self, task_result: dict) -> dict:
        """Step 9: deterministic kernel updates based on the task outcome."""
        success = bool(task_result.get("success", True))
        summary = str(task_result.get("summary", ""))[:120]
        # Record agency
        from datetime import datetime, timezone
        self.kernel.self_state.agency_log.append({
            "at": datetime.now(timezone.utc).isoformat(),
            "summary": summary,
            "success": success,
        })
        # Cap log size
        if len(self.kernel.self_state.agency_log) > 200:
            del self.kernel.self_state.agency_log[:-200]
        return {"agency_log_len": len(self.kernel.self_state.agency_log)}

    def _step_consolidate(self, task_result: dict) -> dict:
        """Step 10: consolidator stub.

        Full dual-tier memory implementation is Phase 7. For now we
        stage the task result in the kernel's consolidation buffer
        so downstream code can see what WOULD be written.
        """
        self.kernel.consolidation_buffer.pending_episodes.append(
            {"result_summary": str(task_result.get("summary", ""))[:200]}
        )
        # Cap buffer to avoid unbounded growth before Phase 7 drains it.
        if len(self.kernel.consolidation_buffer.pending_episodes) > 100:
            del self.kernel.consolidation_buffer.pending_episodes[:-100]
        return {"pending_episodes": len(
            self.kernel.consolidation_buffer.pending_episodes
        )}

    def _step_reflect(self) -> dict:
        """Step 11: periodic self-narrative reflection.

        Gates on NARRATIVE_DRIFT_CHECK_FREQUENCY. When the current
        loop_count is divisible by the frequency, run a narrative
        audit. Until the narrative_audit module is fully wired
        (Phase 8), record that the check would have fired.
        """
        frequency = int(SUBIA_CONFIG["NARRATIVE_DRIFT_CHECK_FREQUENCY"])
        should_audit = (self.kernel.loop_count > 0
                        and self.kernel.loop_count % frequency == 0)
        return {"audit_due": should_audit, "loop_count": self.kernel.loop_count}

    # ── Context injection ────────────────────────────────────────

    def _build_compressed_context(self) -> dict:
        """Context block for compressed loop: scene digest only."""
        return {
            "scene_summary": [
                {"summary": getattr(i, "summary", "")[:60],
                 "salience": round(getattr(i, "salience", 0.0), 2)}
                for i in self.kernel.focal_scene()
            ],
            "loop_type": "compressed",
        }

    def _build_full_context(self) -> dict:
        """Context block for full loop: scene, affect, prediction,
        cascade recommendation, dispatch verdict.
        """
        ctx = self._build_compressed_context()
        ctx["loop_type"] = "full"
        last_pred = self.kernel.predictions[-1] if self.kernel.predictions else None
        if last_pred is not None:
            ctx["prediction"] = {
                "confidence": round(getattr(last_pred, "confidence", 0.5), 2),
                "expected": getattr(last_pred, "predicted_outcome", {}),
            }
        ctx["cascade_recommendation"] = getattr(
            self, "_cascade_recommendation", "maintain",
        )
        decision = getattr(self, "_dispatch_decision", None)
        if decision is not None:
            ctx["dispatch"] = {
                "verdict": getattr(decision, "verdict", "ALLOW"),
                "reason": getattr(decision, "reason", ""),
            }
        h = self.kernel.homeostasis
        over_threshold = {
            v: round(d, 2) for v, d in h.deviations.items()
            if abs(d) > SUBIA_CONFIG["HOMEOSTATIC_DEVIATION_THRESHOLD"]
        }
        if over_threshold:
            ctx["homeostatic_deviations"] = over_threshold
        return ctx

    # ── Plumbing: step runner with error containment ─────────────

    def _run(self, result: CILResult, name: str, fn: Callable[[], dict]) -> None:
        """Execute one step; errors never propagate to the agent."""
        t0 = self._now()
        try:
            details = fn() or {}
            elapsed = (self._now() - t0) * 1000.0
            result.add(StepOutcome(step=name, ok=True,
                                   elapsed_ms=elapsed, details=dict(details)))
        except Exception as exc:
            elapsed = (self._now() - t0) * 1000.0
            logger.exception("CIL step '%s' raised: %s", name, exc)
            result.add(StepOutcome(step=name, ok=False,
                                   elapsed_ms=elapsed, error=repr(exc)))
