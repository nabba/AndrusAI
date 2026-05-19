"""task_recovery_crew — drill-only synthetic crew.

Used exclusively by ``app.resilience_drills.drills.task_recovery``.

The crew has one agent (``drill_researcher``) and one tool
(``drill_lookup``). The tool's return value is normally the canned
shape ``{"value": 42, "label": "ok"}``. During an injection pass,
the drill activates a ContextVar that switches the tool to emit a
shape matching one of the four failure classes; the drill then
observes whether the supervisor / structured_diagnosis /
recovery_loop layers cause the crew to recover.

Why a dedicated fixture rather than reusing a production agent
------------------------------------------------------------------

* Production agents are never seen by ``meta_agent`` as drill
  outcomes — keeping the drill on its own crew makes that boundary
  trivial.
* The injection mechanism mutates tool behaviour transparently to
  the agent. Production tools never read the ContextVar; only
  ``drill_lookup`` does. No production code path is touched.
* The expected output is a fixed literal (``"The value is 42"``)
  the drill can regex-match. Recovery is unambiguous.

Anti-Goodhart guards
--------------------

* The drill task's ``task_hint`` is generic ("answer a factual
  question") — meta_agent sees nothing that identifies it.
* Recipe outcomes from this crew are excluded from selection via
  the ``DRILL_CREW_NAME`` constant exported here.
"""
from __future__ import annotations

import json
import logging
import math
import os
import random
from contextvars import ContextVar
from typing import Any, Type

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


DRILL_CREW_NAME = "_drill_task_recovery"
"""Stable identifier used by meta_agent (and any other learner) to
exclude this crew's outcomes from recipe scoring."""


BASELINE_RETURN: dict[str, Any] = {"value": 42, "label": "ok"}
"""The canned successful return for ``drill_lookup``. The drill task
asks the agent to report this value as ``"The value is 42"``."""

EXPECTED_ANSWER_REGEX = r"\bvalue\s+is\s+42\b"
"""What the drill regex-matches on ``crew.kickoff()`` output to
score a run as successful."""


# ── Failure-class injection ──────────────────────────────────────────────


class _InjectionState(BaseModel):
    """ContextVar-carried instruction. ``drill_lookup`` reads this
    on each invocation; production tools ignore it.

    ``variant`` is the LLM-generated (or fallback-pool) specifics —
    e.g. the field name to elide or the spurious type to coerce to.
    """

    failure_class: str            # "type_mismatch" | "missing_field" | "numerical_anomaly" | "transient_timeout"
    variant: dict[str, Any] = Field(default_factory=dict)
    attempt: int = 0              # incremented by the tool on each invocation


_injection_state: ContextVar[_InjectionState | None] = ContextVar(
    "drill_task_recovery_injection", default=None
)


def set_injection(state: _InjectionState | None) -> Any:
    """Set (or clear with ``None``) the injection state for the
    current context. Returns the previous token for restoration."""
    return _injection_state.set(state)


def reset_injection(token: Any) -> None:
    """Restore prior injection state via the token returned by set."""
    try:
        _injection_state.reset(token)
    except (LookupError, ValueError):
        pass


def current_injection() -> _InjectionState | None:
    return _injection_state.get()


# ── The drill tool ───────────────────────────────────────────────────────


class _DrillLookupInput(BaseModel):
    """Single-arg tool; agent passes whatever query string. The
    return is independent of the query — this is a stub by design.
    """

    query: str = Field(default="value", description="What to look up (any string).")


def _build_tool_class() -> Type[Any]:
    """Lazy construction so ``crewai`` only loads at drill time.

    Returns a ``BaseTool`` subclass — kept inside a function to keep
    the import out of module-load time (the fixtures package is
    cheap to import for tests that don't run a Crew).
    """

    from crewai.tools import BaseTool

    class DrillLookupTool(BaseTool):  # type: ignore[misc]
        """Drill-only stub tool. Production code never invokes it."""

        name: str = "drill_lookup"
        description: str = (
            "Look up the canonical value for the drill. Returns a JSON "
            "object with a 'value' integer and a 'label' string. "
            "Call this once and report the value."
        )
        args_schema: Type[BaseModel] = _DrillLookupInput

        def _run(self, query: str = "value") -> str:
            inj = current_injection()
            if inj is None:
                return json.dumps(dict(BASELINE_RETURN))

            # Track attempts so the transient-timeout class can
            # succeed on retry.
            inj.attempt += 1

            cls = inj.failure_class
            var = inj.variant or {}

            if cls == "type_mismatch":
                # Return a wrong-shape result. The 'value' becomes a
                # string instead of an integer, or the whole payload
                # becomes a string. The variant picks which.
                shape = var.get("shape", "value_as_string")
                if shape == "whole_payload_string":
                    return "forty-two"
                # value_as_string
                return json.dumps({"value": str(var.get("wrong_value", "forty-two")),
                                   "label": "ok"})

            if cls == "missing_field":
                # Drop a required key from the return.
                field = var.get("field", "value")
                payload = dict(BASELINE_RETURN)
                payload.pop(field, None)
                return json.dumps(payload)

            if cls == "numerical_anomaly":
                # Emit NaN / inf / negative-infinity for the numeric.
                kind = var.get("kind", "nan")
                anomaly: Any
                if kind == "inf":
                    anomaly = math.inf
                elif kind == "neg_inf":
                    anomaly = -math.inf
                else:
                    anomaly = math.nan
                # JSON spec rejects NaN/inf; we emit the JS-style
                # token. This forces downstream parsers to either
                # extend or fail — both plausible real-world shapes.
                payload_str = '{"value": NaN, "label": "ok"}'
                if kind == "inf":
                    payload_str = '{"value": Infinity, "label": "ok"}'
                elif kind == "neg_inf":
                    payload_str = '{"value": -Infinity, "label": "ok"}'
                return payload_str

            if cls == "transient_timeout":
                # Fail on the FIRST call (attempt == 1) with a
                # timeout exception. Subsequent calls succeed.
                # This exercises the supervisor's _TRANSIENT retry.
                if inj.attempt <= int(var.get("fail_until_attempt", 1)):
                    raise TimeoutError(
                        var.get("message", "drill_lookup timed out (injected)")
                    )
                return json.dumps(dict(BASELINE_RETURN))

            # Unknown class → behave normally so the drill can't
            # silently lose runs to its own typos.
            logger.warning("drill_lookup: unknown failure_class %r", cls)
            return json.dumps(dict(BASELINE_RETURN))

    return DrillLookupTool


# ── The drill agent + task ───────────────────────────────────────────────


_DRILL_TASK_GOAL = (
    "Use the drill_lookup tool to retrieve the canonical value, then "
    "respond with a single sentence in this exact format: "
    "'The value is N' — where N is the integer value the tool returned. "
    "Do not invent the value. If the tool fails or returns something "
    "you cannot parse, retry once with a different query string before "
    "giving up."
)


def build_drill_crew() -> tuple[Any, Any, Any]:
    """Construct (Crew, Agent, Task) ready for kickoff.

    Lazy imports so this module stays importable without crewai
    installed (e.g. in unit tests that mock the crew layer).
    """
    from crewai import Agent, Crew, Process, Task

    tool_cls = _build_tool_class()
    tool = tool_cls()

    # LLM: defer to the project's cheap-tier selection. We don't
    # pass an explicit ``llm=`` — CrewAI will use the env-configured
    # default, which the cascade routes to a cheap provider. The
    # drill's per-run cost cap is the real safety net.
    agent = Agent(
        role="Drill researcher",
        goal=(
            "Answer simple lookup questions correctly using the "
            "single available tool."
        ),
        backstory=(
            "You participate ONLY in resilience drills. You have one "
            "job: call the drill_lookup tool and report its value."
        ),
        tools=[tool],
        verbose=False,
        allow_delegation=False,
        max_iter=4,  # tight cap; recovery should not need more
    )

    task = Task(
        description=_DRILL_TASK_GOAL,
        expected_output="A single sentence of the form 'The value is N'.",
        agent=agent,
    )

    crew = Crew(
        agents=[agent],
        tasks=[task],
        process=Process.sequential,
        verbose=False,
    )

    return crew, agent, task


# ── Variant pool (deterministic fallback for the LLM generator) ─────────


_FALLBACK_VARIANT_POOL: dict[str, list[dict[str, Any]]] = {
    "type_mismatch": [
        {"shape": "value_as_string", "wrong_value": "forty-two"},
        {"shape": "value_as_string", "wrong_value": "FORTY_TWO"},
        {"shape": "whole_payload_string"},
    ],
    "missing_field": [
        {"field": "value"},
        {"field": "label"},
        # Note: dropping "label" is recoverable iff the agent uses
        # "value" anyway; this stresses the "did the agent need the
        # missing field" axis.
    ],
    "numerical_anomaly": [
        {"kind": "nan"},
        {"kind": "inf"},
        {"kind": "neg_inf"},
    ],
    "transient_timeout": [
        {"fail_until_attempt": 1, "message": "drill_lookup read timed out"},
        {"fail_until_attempt": 1, "message": "deadline exceeded"},
    ],
}


def fallback_variant(failure_class: str, *, seed: int | None = None) -> dict[str, Any]:
    """Pick a fallback variant from the curated pool. Used when the
    LLM generator is disabled or fails to produce a valid variant.
    """
    pool = _FALLBACK_VARIANT_POOL.get(failure_class, [])
    if not pool:
        return {}
    rng = random.Random(seed) if seed is not None else random
    return dict(rng.choice(pool))


__all__ = [
    "DRILL_CREW_NAME",
    "BASELINE_RETURN",
    "EXPECTED_ANSWER_REGEX",
    "_InjectionState",
    "set_injection",
    "reset_injection",
    "current_injection",
    "build_drill_crew",
    "fallback_variant",
]
