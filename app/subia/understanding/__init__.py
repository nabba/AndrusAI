"""subia.understanding — Phase 12 Proposal 2: Understanding Layer.

Post-ingest second-pass enrichment of wiki pages: causal chain
extraction, implication mining, structural-analogy detection, deep-
question registration. Output is an UnderstandingDepth descriptor that
feeds the Wonder Register and the Predictor (causal predictions).

LLM-driven (Tier-2 reasoning depth). Queued via the idle scheduler;
never runs in the CIL hot path.
"""
from .pass_runner import (
    UnderstandingPassRunner,
    UnderstandingAdapters,
    UnderstandingPassResult,
)

__all__ = [
    "UnderstandingPassRunner", "UnderstandingAdapters",
    "UnderstandingPassResult",
]
