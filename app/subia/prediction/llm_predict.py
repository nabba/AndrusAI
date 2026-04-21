"""
subia.prediction.llm_predict — live LLM predict_fn bound to the
existing llm_factory cascade.

The SubIALoop accepts any Callable[[dict], Prediction] as predict_fn.
Tests use deterministic stubs. Production uses this module's
build_llm_predict_fn() to wire against the cascade (local Ollama
first, API tier, then Claude fallback per llm_factory).

Design:
  - The LLM is invoked through llm_factory.create_specialist_llm
    with role="self_improve" (lowest cost tier per existing selector).
  - The predictor uses structured output: a strict JSON prompt +
    permissive parser + graceful fallback to a low-confidence
    default on any failure.
  - Each predict call is best-effort. A stuck LLM must not crash
    the CIL loop — on failure we return a low-confidence
    Prediction with `cached=False` so the calling layer can still
    flag it as a genuine miss.
  - The module defers ALL llm_factory imports until the predictor
    is actually invoked, so tests that don't want a live LLM can
    use the stub predictors without importing the cascade.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Callable, Iterable

from app.subia.kernel import (
    HomeostaticState,
    Prediction,
    SceneItem,
    SelfState,
    SubjectivityKernel,
)

logger = logging.getLogger(__name__)


# ── Public factory ────────────────────────────────────────────────

def build_llm_predict_fn(
    *,
    role: str = "self_improve",
    max_tokens: int = 512,
    llm: object | None = None,
) -> Callable[[dict], Prediction]:
    """Return a predict_fn compatible with SubIALoop's Step 5.

    Args:
        role:       LLM cascade role. 'self_improve' defaults to the
                    local Ollama tier per the existing selector — which
                    matches Amendment B's "only step 5 is tier 1" spec.
        max_tokens: Cap on prediction response length.
        llm:        Pre-constructed LLM to use (for tests). When None,
                    a live LLM is created lazily on the first call
                    via llm_factory.

    The returned function closes over `llm` and a tiny cache of the
    last failure time so we don't hammer a failing cascade every
    request.
    """

    state = {"llm": llm, "last_failure_at": 0.0}

    def _predict(ctx: dict) -> Prediction:
        prompt = _build_prompt(ctx)

        try:
            llm_inst = state["llm"]
            if llm_inst is None:
                # Lazy import: only the live path touches llm_factory.
                from app.llm_factory import create_specialist_llm
                llm_inst = create_specialist_llm(
                    max_tokens=max_tokens, role=role,
                )
                state["llm"] = llm_inst

            response_text = _call_llm(llm_inst, prompt)
            parsed = _parse_json_block(response_text)
        except Exception as exc:
            logger.exception(
                "subia.prediction.llm_predict: LLM call failed (%s); "
                "falling back to low-confidence default",
                exc,
            )
            return _fallback_prediction(ctx, reason=f"llm-error: {exc}")

        return _parsed_to_prediction(parsed, ctx)

    return _predict


# ── Prompt assembly ───────────────────────────────────────────────

_PROMPT_TEMPLATE = """You are the SubIA predictor. Before an agent runs
a task, you generate a structured prediction of what will happen.

Operation: {agent_role} → {task_description}

Current focal scene (most-salient items):
{scene_summary}

Self-state digest:
  active_commitments: {commitment_count}
  current_goals:      {goals}

Homeostatic variables above deviation threshold:
{homeostasis_summary}

Recent prediction accuracy: {recent_accuracy:.2f}
(Calibrate your confidence: if recent accuracy is low, output lower
confidence.)

Respond with ONLY a JSON object — no prose, no code fences, no
commentary. The JSON must match this schema:

{{
  "world_changes": {{
    "wiki_pages_affected": ["list of wiki paths likely to be created or updated"],
    "contradictions_expected": 0,
    "summary": "one-sentence world outcome"
  }},
  "self_changes": {{
    "confidence_change": 0.0,
    "new_commitments": [],
    "summary": "one-sentence self-state outcome"
  }},
  "homeostatic_effects": {{}},
  "confidence": 0.5
}}
"""


def _build_prompt(ctx: dict) -> str:
    agent_role = str(ctx.get("agent_role", "agent"))
    description = str(ctx.get("task_description", ""))[:400]
    scene = ctx.get("scene") or ()
    self_state = ctx.get("self_state")
    homeo = ctx.get("homeostasis")
    history = ctx.get("prediction_history") or ()

    prompt = _PROMPT_TEMPLATE.format(
        agent_role=agent_role,
        task_description=description,
        scene_summary=_scene_summary(scene),
        commitment_count=_commitment_count(self_state),
        goals=_goals(self_state),
        homeostasis_summary=_homeostasis_summary(homeo),
        recent_accuracy=_recent_accuracy(history),
    )

    # Optional context enrichment (Phase 13/14 bridges). Temporal +
    # technical state reach the predictor here so predictions respect
    # circadian mode, compute pressure, and cascade-tier availability.
    extra = ctx.get("extra_prompt_context")
    if isinstance(extra, str) and extra.strip():
        prompt = prompt + "\n" + extra.strip() + "\n"
    return prompt


def _scene_summary(scene: Iterable) -> str:
    items = list(scene)[:5]
    if not items:
        return "  (scene empty)"
    return "\n".join(
        f"  - [{round(getattr(i, 'salience', 0), 2)}] "
        f"{getattr(i, 'summary', '')[:80]}"
        for i in items
    )


def _commitment_count(self_state) -> int:
    if not isinstance(self_state, SelfState):
        return 0
    return sum(
        1 for c in self_state.active_commitments
        if getattr(c, "status", "active") == "active"
    )


def _goals(self_state) -> str:
    if not isinstance(self_state, SelfState):
        return "(none)"
    goals = list(self_state.current_goals)[:3]
    return ", ".join(map(str, goals)) if goals else "(none)"


def _homeostasis_summary(homeo) -> str:
    if not isinstance(homeo, HomeostaticState) or not homeo.deviations:
        return "  (none)"
    from app.subia.config import SUBIA_CONFIG
    threshold = float(SUBIA_CONFIG["HOMEOSTATIC_DEVIATION_THRESHOLD"])
    interesting = sorted(
        ((v, d) for v, d in homeo.deviations.items() if abs(d) > threshold),
        key=lambda pair: abs(pair[1]), reverse=True,
    )[:4]
    if not interesting:
        return "  (all within equilibrium)"
    return "\n".join(f"  {v}: {d:+.2f}" for v, d in interesting)


def _recent_accuracy(history) -> float:
    resolved = [
        p for p in history
        if getattr(p, "resolved", False)
        and getattr(p, "prediction_error", None) is not None
    ]
    if not resolved:
        return 0.5
    recent = resolved[-20:]
    avg_error = sum(p.prediction_error for p in recent) / len(recent)
    return max(0.0, min(1.0, 1.0 - avg_error))


# ── LLM call ──────────────────────────────────────────────────────

def _call_llm(llm: object, prompt: str) -> str:
    """Invoke the LLM. Supports both crewai-style .call/.__call__
    and a plain callable returning a string or dict.
    """
    for attr in ("call", "__call__"):
        fn = getattr(llm, attr, None)
        if not callable(fn):
            continue
        try:
            out = fn(prompt)
        except Exception:
            continue
        if isinstance(out, str):
            return out
        if isinstance(out, dict):
            # Some llm_factory paths return dict with {text: ...}
            for k in ("text", "content", "output", "response"):
                if k in out and isinstance(out[k], str):
                    return out[k]
        if hasattr(out, "content"):
            return str(out.content)
    # Fallback: str(llm) won't help, just signal failure.
    raise RuntimeError(
        f"llm {type(llm).__name__} has no usable .call/.__call__",
    )


# ── JSON parsing (permissive) ─────────────────────────────────────

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_json_block(text: str) -> dict:
    """Extract the first {...} block and json.loads it. Permissive:
    trims code fences, leading/trailing prose.
    """
    if not text:
        return {}
    cleaned = text.strip()
    # Strip common fences: ```json ... ``` or ``` ... ```
    if cleaned.startswith("```"):
        # drop the first fence line
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    # Find {...}
    match = _JSON_BLOCK_RE.search(cleaned)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}


# ── Prediction assembly ───────────────────────────────────────────

def _parsed_to_prediction(parsed: dict, ctx: dict) -> Prediction:
    agent_role = str(ctx.get("agent_role", "agent"))
    description = str(ctx.get("task_description", ""))[:80]

    if not isinstance(parsed, dict) or not parsed:
        return _fallback_prediction(ctx, reason="parse-miss")

    world = parsed.get("world_changes") or {}
    self_changes = parsed.get("self_changes") or {}
    homeostatic = parsed.get("homeostatic_effects") or {}

    # Clamp confidence to [0,1]; fall back to 0.5 if missing/invalid.
    try:
        confidence = float(parsed.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    # Homeostatic effects must be {str: float}; drop anything else.
    cleaned_homeo: dict[str, float] = {}
    for k, v in dict(homeostatic).items():
        try:
            cleaned_homeo[str(k)] = float(v)
        except (TypeError, ValueError):
            continue

    return Prediction(
        id=f"pred-{uuid.uuid4()}",
        operation=f"{agent_role}:{description}",
        predicted_outcome=dict(world) if isinstance(world, dict) else {},
        predicted_self_change=(
            dict(self_changes) if isinstance(self_changes, dict) else {}
        ),
        predicted_homeostatic_effect=cleaned_homeo,
        confidence=confidence,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def _fallback_prediction(ctx: dict, *, reason: str = "") -> Prediction:
    """Low-confidence default used when the LLM or parser fails."""
    agent_role = str(ctx.get("agent_role", "agent"))
    description = str(ctx.get("task_description", ""))[:80]
    return Prediction(
        id=f"pred-{uuid.uuid4()}",
        operation=f"{agent_role}:{description}",
        predicted_outcome={"summary": "(fallback — no LLM response)"},
        predicted_self_change={"confidence_change": 0.0},
        predicted_homeostatic_effect={},
        confidence=0.3,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
