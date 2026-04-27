"""subia.introspection — Phase 17: Self-Introspection Routing.

Closes the user-visible failure mode shown by the "what increased your
frustration?" Signal conversation: the bot literally had `frustration=0.6293`
in `app.subia.homeostasis.state.get_state()` but answered "I do not have
personal feelings, emotions, or the capacity to experience frustration."

The architecture to prevent this exists across the SubIA stack — homeostasis
state is computed and persisted, the legacy 4-variable system has 274 tasks
of accumulated history, the SubIA-native 9-variable kernel runs on every
crew task. Phase 17 ROUTES the chat surface through it.

Four components, all unit-testable via injected adapters:

  detector.py   Cheap deterministic introspection-question classifier
                (keyword regex + scoring). Hot-path safe; zero LLM.
  context.py    IntrospectionContext aggregates legacy 4-var homeostasis +
                kernel 9-var state + behavioural modifiers + recent
                failures + chronicle excerpt + Phase 12 discovered
                limitations + temporal/circadian state. Each gather is
                defensive — missing data degrades to empty fields.
  formatter.py  Pure transformer: IntrospectionContext → system-prompt
                prefix that names variables in Phase 11 conventions
                (functional signals, not subjective feelings) and cites
                concrete numbers + active behavioural modifiers + likely
                causal contributors.
  pipeline.py   IntrospectionPipeline — public face. Two operations:
                inspect(user_message) → optional system-prompt prefix
                inject(user_message)  → augmented message with prefix.

Hot-path cost: ~0 LLM tokens (detection + gathering + formatting are
all deterministic). Adds ~150-300 tokens of system-prompt context only
when the detector fires.

The package is FEATURE-FLAGGED off by default. Activation by setting
SUBIA_INTROSPECTION_ENABLED=1. When disabled, the chat handler runs
unchanged — `inject_introspection()` returns the original message.
"""
from .detector import (
    is_introspection_question,
    IntrospectionMatch,
    classify_introspection,
)
from .context import IntrospectionContext, gather_context
from .formatter import format_introspection_note
from .pipeline import (
    IntrospectionPipeline,
    IntrospectionPipelineConfig,
    IntrospectionResult,
)

__all__ = [
    "is_introspection_question", "IntrospectionMatch", "classify_introspection",
    "IntrospectionContext", "gather_context",
    "format_introspection_note",
    "IntrospectionPipeline", "IntrospectionPipelineConfig", "IntrospectionResult",
]
