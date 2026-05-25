"""LLM-based goal decomposition (Phase 2 piece 2e, 2026-05-20).

The v1 deterministic planner (``planner.plan``) wraps every goal in a
single ExecutorStep — relying on Commander's crew routing to do any
needed decomposition downstream. That works for tasks that fit cleanly
into one crew's lane but degrades for multi-step goals like "summarise
the news AND draft a reply" where each clause wants different routing.

v2 (this module) asks Claude Haiku 4.5 to break the goal into 1-5
sub-goals + optional crew hints. The driver then executes each
sub-goal as a separate ExecutorStep, with budget consumption + per-
step failure isolation kicking in naturally.

Key design choices:

* **Failure-isolated, fail-quiet**. Any error in the LLM path — import
  error, network blip, bad JSON, empty response, oversized response —
  falls back to v1 single-step. The executor never *can't run* because
  of a v2 problem.

* **JSON-strict output**. The LLM is asked for a JSON array of objects.
  We accept fenced (```json ... ```) and bare JSON. Schema:
  ``[{"description": str (≥4 chars), "crew_hint": str (optional, ≤30 chars)}]``.
  Anything that doesn't parse → fall back.

* **Bounded output**. Max 5 steps. The cap is the safety bound that
  prevents a runaway decomposition from filling the run's plan with
  dozens of micro-tasks the operator never asked for.

* **Master switch + injectable hook**. ``runtime_settings``
  ``autonomous_executor_llm_planner_enabled`` (default False) gates
  whether ``planner.get_default_planner`` returns v1 or v2. ``llm_call``
  is injectable for tests.

* **Cost ceiling**. The single Haiku 4.5 call is ~$0.0005-0.001
  worst case. Bounded by ``max_tokens=600``.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Callable, Optional

from app.autonomous_executor.models import (
    ExecutorRun,
    ExecutorStep,
    StepStatus,
)
from app.autonomous_executor.planner import plan as v1_plan

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = (
    "You are a goal decomposer for an autonomous task executor. The "
    "operator gives you a goal; you break it into 1-5 concrete "
    "sub-goals that can be executed sequentially. Each sub-goal must "
    "be self-contained (a downstream LLM will dispatch it without "
    "any context about the others).\n\n"
    "Rules:\n"
    "  * Return a JSON array of objects with shape "
    "{\"description\": str, \"crew_hint\": str}.\n"
    "  * Description must be ≥4 characters, clear imperative voice.\n"
    "  * crew_hint is optional (use empty string when unsure). Valid "
    "hints: research, coding, writing, pim, financial, devops, "
    "media, repo_analysis, company_dossier, desktop.\n"
    "  * Maximum 5 sub-goals. Use 1 when the goal is already atomic; "
    "don't artificially split goals that fit in one dispatch.\n"
    "  * No prose, no markdown, no commentary — JSON only."
)

_USER_TEMPLATE = (
    "Decompose this goal into 1-5 sub-goals (return JSON array only):\n\n"
    "{goal}"
)


# Strict-JSON cap — defends against an LLM that ignores instructions
# and emits a wall of text. Anything beyond this is treated as garbage.
_MAX_RESPONSE_BYTES = 4000
_MAX_STEPS = 5
_MIN_DESC_LEN = 4
_MAX_DESC_LEN = 500
_MAX_HINT_LEN = 30


# Type alias mirroring driver.PlannerFn.
LLMCallFn = Callable[[str, str], str]


def _default_llm_call(system_prompt: str, user_prompt: str) -> str:
    """Cheap-tier Anthropic Haiku 4.5 call. Returns empty string on
    any failure (import error, network, etc.).

    Verified Plan Gap #5 (2026-05-22): the Anthropic daily cap is
    enforced by the factory's ``.messages.create`` pre-check; on
    cap-out the existing ``except Exception`` below returns "" — the
    v2 LLM planner naturally falls back to the deterministic v1
    planner.
    """
    try:
        from app.llm_factory import anthropic_client_for_role
        client = anthropic_client_for_role(role="cheap-vetting", task_hint="planner")
        msg = client.messages.create(
            max_tokens=600,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text_parts = [
            getattr(b, "text", "")
            for b in (msg.content or [])
            if getattr(b, "type", "") == "text"
        ]
        return "".join(text_parts).strip()
    except Exception:
        logger.debug(
            "autonomous_executor: LLM planner call failed",
            exc_info=True,
        )
        return ""


_FENCE_RE = re.compile(r"^```(?:json)?\s*\n(.*?)\n```\s*$", re.DOTALL)


def _strip_code_fence(text: str) -> str:
    """Tolerate ```json ... ``` wrappers around the JSON payload.
    Returns the inner content; if no fence detected, returns input."""
    m = _FENCE_RE.match(text.strip())
    if m:
        return m.group(1).strip()
    return text.strip()


def _parse_llm_steps(raw: str) -> list[ExecutorStep]:
    """Parse the LLM's JSON output into validated ExecutorStep
    instances. Returns ``[]`` on any validation failure — caller
    falls back to v1 deterministic single-step.

    Validation:
      * raw is non-empty + within size cap
      * JSON parses as a non-empty list
      * each entry is a dict with ``description`` of valid length
      * ``crew_hint`` is optional + within length cap
      * total entries ≤ _MAX_STEPS
    """
    if not raw or len(raw.encode("utf-8")) > _MAX_RESPONSE_BYTES:
        return []
    stripped = _strip_code_fence(raw)
    try:
        data = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(data, list) or not data:
        return []
    if len(data) > _MAX_STEPS:
        return []

    out: list[ExecutorStep] = []
    for entry in data:
        if not isinstance(entry, dict):
            return []
        desc = entry.get("description", "")
        if not isinstance(desc, str):
            return []
        desc = desc.strip()
        if len(desc) < _MIN_DESC_LEN or len(desc) > _MAX_DESC_LEN:
            return []
        hint_raw = entry.get("crew_hint", "")
        if not isinstance(hint_raw, str):
            return []
        hint = hint_raw.strip()
        if len(hint) > _MAX_HINT_LEN:
            return []
        # Note: step_id is regenerated by the driver via add_step,
        # so we use a placeholder here.
        out.append(ExecutorStep(
            step_id=f"step-{len(out) + 1:03d}",
            description=desc,
            crew_hint=hint,
            status=StepStatus.PENDING,
        ))
    return out


def llm_plan(
    goal: str,
    run: ExecutorRun,
    *,
    llm_call: Optional[LLMCallFn] = None,
) -> list[ExecutorStep]:
    """LLM-based goal decomposition. Drop-in replacement for
    :func:`app.autonomous_executor.planner.plan` — same signature,
    same return type, same error semantics.

    Returns 1-5 ExecutorStep instances on success. Falls back to v1
    single-step on any failure (import error, network error, malformed
    JSON, validation failure, empty response). The fall-back means the
    executor never blocks on LLM availability — operators can enable
    the v2 planner without worrying about hard dependency on Anthropic
    being reachable.
    """
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("llm_plan: goal cannot be empty")

    call = llm_call or _default_llm_call
    user_prompt = _USER_TEMPLATE.format(goal=goal.strip())

    try:
        raw = call(_SYSTEM_PROMPT, user_prompt)
    except Exception:
        logger.debug(
            "autonomous_executor: llm_call raised; falling back to v1",
            exc_info=True,
        )
        return v1_plan(goal, run)

    steps = _parse_llm_steps(raw)
    if not steps:
        # Any failure → v1 single-step. Never block the executor.
        return v1_plan(goal, run)
    return steps
