"""llm_message_order.py — satisfy the provider rule on system-message position.

Every OpenRouter upstream rejects the same request shape:

    messages.1: role 'system' must precede an 'assistant' message or end the
    array; the directive-only form (content: [] with output_config) is accepted
    at any position

Observed identically from Azure, Amazon Bedrock, Google and Anthropic, so it is
our request shape rather than a provider quirk. Live impact on 2026-07-25: **the
critic crew failed 100% of the time** — all five invocations on the golden set —
and failed *silently*, because ``critic_crew.review()`` catches the exception and
returns the crew's original output. Adversarial review had not run on any
high-difficulty answer, and nothing surfaced it until the eval harness gained a
provenance join. 94 such errors in one afternoon.

This was diagnosed as **D4** in ``reports/ANSWER_QUALITY_DIAGNOSIS_2026-07-24.md``
("native-tools path builds message arrays with a system message after assistant
messages"), deferred, and had been live ever since.

The transform
-------------
A system message that appears after the first assistant message, and is not the
final element, is hoisted to the front — preserving the relative order of the
messages that move and of those that stay. Content is never edited or dropped.

Why hoisting rather than re-roling: the offending messages are context summaries
(``history_compression.to_langchain_messages`` emits ``[Earlier context summary]``
and ``[Previous exchange summary]`` as system messages interleaved between
topics, so one lands after an assistant turn whenever a summarised topic follows
an unsummarised one). The front of the array is where that context belongs.
Converting them to ``user`` would preserve position but change who is speaking,
which is a larger semantic change than moving guidance to the top.

Deliberately conservative: no-ops when there is no assistant message (a system
message anywhere is then legal), and leaves a trailing system message alone
because the rule explicitly permits it.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _role_of(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("role") or "")
    return str(getattr(message, "role", "") or "")


def normalize_system_message_order(messages: Any) -> tuple[Any, int]:
    """Return ``(messages, moved_count)`` with illegal system positions fixed.

    Non-mutating: builds a new list only when something actually moves.
    """
    if not isinstance(messages, list) or len(messages) < 2:
        return messages, 0

    roles = [_role_of(m) for m in messages]
    try:
        first_assistant = roles.index("assistant")
    except ValueError:
        # No assistant message — a system message is legal at any position.
        return messages, 0

    last_index = len(messages) - 1
    offending = [
        index
        for index, role in enumerate(roles)
        if role == "system" and index > first_assistant and index != last_index
    ]
    if not offending:
        return messages, 0

    offending_set = set(offending)
    hoisted = [messages[i] for i in offending]
    remainder = [m for i, m in enumerate(messages) if i not in offending_set]
    return hoisted + remainder, len(hoisted)


def normalize_call_args(args: tuple, kwargs: dict, *, model: str = "") -> tuple:
    """Apply the fix to a ``crewai.LLM.call`` argument pair.

    ``messages`` may be positional or a kwarg, mirroring
    ``BudgetAwareCompletion._inject_cache_control``. Failure-soft: any error
    leaves the call untouched, because a normalisation helper must never be able
    to break a request it was only meant to repair.
    """
    try:
        if args:
            fixed, moved = normalize_system_message_order(args[0])
            if moved:
                _log(moved, model)
                return (fixed, *args[1:]), kwargs
            return args, kwargs
        if "messages" in kwargs:
            fixed, moved = normalize_system_message_order(kwargs["messages"])
            if moved:
                _log(moved, model)
                kwargs = {**kwargs, "messages": fixed}
        return args, kwargs
    except Exception:  # pragma: no cover — defensive
        logger.debug("normalize_call_args failed; leaving messages untouched",
                     exc_info=True)
        return args, kwargs


def _log(moved: int, model: str) -> None:
    # INFO not DEBUG: this silently broke the critic for days, so the repair
    # should be visible in normal operation.
    logger.info(
        "llm_message_order: hoisted %d misplaced system message(s) to the front "
        "for %s (provider rule: system must precede an assistant message or end "
        "the array)", moved, model or "unknown-model",
    )
