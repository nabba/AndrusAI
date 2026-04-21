"""
llm_events.py — Single cross-cutting subscriber for CrewAI's LLM event bus.

Motivation
----------
CrewAI ships many LLM call paths (``AnthropicCompletion``, ``OpenAICompletion``,
``GeminiCompletion``, ``AzureCompletion``, ``BedrockCompletion`` and the
generic ``crewai.LLM`` that wraps LiteLLM for everything else).  Each one is
a separate ``BaseLLM`` subclass with its own call/response plumbing.

Historically we attached cross-cutting observability hooks to each path
individually — token/cost tracking in three places, heartbeat in three
places — and the fragmentation bit us: any missed path stopped firing the
hook, and the observer (e.g. the progress-gated timeout in
``handle_task``) couldn't tell "genuinely quiet" from "instrumented wrong".

This module replaces the scattered hooks with a single subscriber to
CrewAI's native event bus.  ``BaseLLM._emit_call_completed_event`` and
``BaseLLM._emit_call_failed_event`` are invoked by every shipped provider
AND by the LiteLLM-wrapping generic ``crewai.LLM``, so one subscriber
covers every upstream provider CrewAI knows about — including future
ones we don't have to instrument explicitly.

What this subscriber does
-------------------------
1. **Activity heartbeat** — records `record_llm_activity()` on every
   successful OR failed call.  The progressive timeout in
   ``app.main.handle_task`` reads this to distinguish "task alive" from
   "task stalled".
2. **Token + cost accounting** — on completed calls, extracts
   ``usage`` from the event, computes cost via the catalog, writes into
   ``llm_benchmarks.record_tokens`` (daily totals / dashboard), and
   bumps the per-request tracker (``_request_cost``) for commander-level
   cost aggregation.  Cost accounting lives here rather than in
   ``rate_throttle._record_token_usage`` because the event bus fires
   exactly once per call regardless of provider — the litellm callback
   only fires for LiteLLM-mediated calls, and the
   ``BaseLLM._track_token_usage_internal`` hook only fires for
   CrewAI-native providers.  One subscriber, both paths covered, no
   double-counting.

What this subscriber does NOT do (by design)
--------------------------------------------
* **Benchmark scoring** (the per-task-type success/failure rows in
  ``llm_benchmarks``) stays in ``rate_throttle`` because it needs
  accurate ``latency_ms`` that the event payload doesn't carry.
* **Training-data capture** stays in ``rate_throttle`` because it
  consumes the raw LiteLLM response shape (prompt+completion text,
  tool-use blocks) which the event doesn't fully reconstruct.
* **Credit-exhausted detection** is handled at the LLM layer by
  ``CreditAwareAnthropicCompletion`` — not here — because the decision
  to fail over must happen synchronously inside ``.call()``, not
  asynchronously from a subscriber.

Non-CrewAI bypass paths
-----------------------
The only LLM call site in this codebase that is NOT a ``BaseLLM``
subclass is ``app.llm_factory._AdapterLLM`` (host-bridge MLX inference
for promoted LoRA adapters).  It calls ``record_llm_activity()``
directly in its ``call()`` body — see that class for the explicit
instrumentation.  No other bypass exists today.
"""
from __future__ import annotations

import logging

from crewai.events.event_bus import crewai_event_bus
from crewai.events.types.llm_events import (
    LLMCallCompletedEvent,
    LLMCallFailedEvent,
)

from app.rate_throttle import record_llm_activity

logger = logging.getLogger(__name__)


# Decorator registration is idempotent in CrewAI's event bus, but we still
# guard against double-install so the log line isn't noisy.
_installed: bool = False


def install() -> None:
    """Subscribe the cross-cutting observability handlers to CrewAI's
    LLM event bus.

    Call once at gateway startup.  Safe to call again — re-registration
    is a no-op.
    """
    global _installed
    if _installed:
        return

    @crewai_event_bus.on(LLMCallCompletedEvent)
    def _on_completed(source, event):  # noqa: ARG001 — CrewAI signature
        record_llm_activity()
        _record_cost_from_event(event)

    @crewai_event_bus.on(LLMCallFailedEvent)
    def _on_failed(source, event):  # noqa: ARG001
        # Failures count as activity too.  A task that's actively hitting
        # an error (credit-exhausted, rate-limited, transient network
        # glitch) is still cycling — the orchestrator will retry, fail
        # over, or escalate.  Stall detection should only fire when
        # NEITHER success NOR failure has been observed for an extended
        # period, i.e. the orchestrator is frozen on non-LLM code.
        record_llm_activity()
        # No cost to record on failure — no tokens came back.

    _installed = True
    logger.info(
        "observability.llm_events: subscribed to CrewAI event bus "
        "(LLMCallCompletedEvent + LLMCallFailedEvent) — unified path for "
        "heartbeat, token accounting, and per-request cost aggregation "
        "across every BaseLLM provider (native + LiteLLM-mediated)."
    )


# ── Cost / token accounting ─────────────────────────────────────────


def _record_cost_from_event(event) -> None:
    """Parse ``event.usage`` and route token + cost into the same
    downstream sinks the legacy per-path hooks used.

    Expected payload keys (CrewAI normalises across providers, but we
    still fall back to common alternates for robustness):
      prompt_tokens / input_tokens
      completion_tokens / output_tokens
    """
    try:
        usage = getattr(event, "usage", None) or {}
        if not usage:
            return
        model = getattr(event, "model", None) or "unknown"
        prompt = int(
            usage.get("prompt_tokens")
            or usage.get("input_tokens")
            or usage.get("prompt_token_count")
            or 0
        )
        completion = int(
            usage.get("completion_tokens")
            or usage.get("output_tokens")
            or usage.get("candidates_token_count")
            or 0
        )
        total = prompt + completion
        if total <= 0:
            return

        # Lazy imports: keep this module's startup cost tiny and avoid
        # circular import entanglement with rate_throttle.
        from app.rate_throttle import _find_cost, _request_cost
        from app.llm_benchmarks import record_tokens

        cost_usd = 0.0
        cost_info = _find_cost(model) or _find_cost(f"anthropic/{model}")
        if cost_info:
            cost_usd = (
                (prompt / 1_000_000) * cost_info[0]
                + (completion / 1_000_000) * cost_info[1]
            )

        # Daily token/cost totals (dashboard aggregation, quota checks).
        record_tokens(model, prompt, completion, cost_usd)

        # Per-request tracker for Commander-level cost roll-ups so the
        # final ticket record shows the true cost of the whole request.
        tracker = _request_cost.get(None)
        if tracker is not None:
            tracker.record(model, prompt, completion, cost_usd)
    except Exception:
        logger.debug(
            "observability.llm_events: cost accounting failed for event",
            exc_info=True,
        )
