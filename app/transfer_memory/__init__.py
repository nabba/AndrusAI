"""Transfer Insight Layer (Phase 17).

Compiles cross-domain "Insight" memories from heterogeneous execution
history — healing knowledge, evolution successes/failures, grounding
corrections, and learning-gap resolutions — into the existing Skill KB
pipeline, with sanitisation preventing project facts from becoming
global meta-memory.

Reference: arXiv:2606.21099-style Memory Transfer Learning for agentic
systems. Cross-domain transfer carries process knowledge ("verify
external numeric claims before answering") rather than facts ("Tallink
share price"). Facts stay local; practices transfer globally.

Cost discipline:
  - Triggers append events synchronously on the write path (microseconds).
  - The compiler runs only as an idle-scheduler HEAVY job, gated by a
    24h cadence guard.
  - The Learner runs under a forced ``llm_mode="free"`` scope so compile
    LLM cost is bounded to the local Ollama + free-tier OpenRouter cascade.

Phase 17a writes drafts to ``workspace/transfer_memory/shadow_drafts.jsonl``
without going through the integrator; promotion to live KBs happens in
Phase 17c after operator review.

IMMUTABLE — infrastructure-level subsystem.
"""

from app.transfer_memory.types import (
    TransferEvent,
    TransferKind,
    TransferScope,
    NegativeTransferTag,
    domain_for_kind,
)
from app.transfer_memory.queue import append_event

__all__ = (
    "TransferEvent",
    "TransferKind",
    "TransferScope",
    "NegativeTransferTag",
    "domain_for_kind",
    "append_event",
)
