"""subia.grounding — Phase 15: Factual Grounding & Correction Memory.

Closes the failure mode demonstrated by the Tallink-share-price
conversation:

  - Bot fabricated three different prices (€0.60 / €0.62 / €0.65) for
    the same date with three different fabricated source attributions.
  - User-supplied correction (€0.595 from Nasdaq Baltic) was verbally
    acknowledged but never persisted — the next turn regressed to the
    original lie.

The architecture to prevent this exists across earlier phases (HOT-3
dispatch gate, Mem0 curated tier, drift detection, asymmetric belief
confirmation). Phase 15 ROUTES the chat surface through them.

Five components, all unit-testable via injected adapters:

  claims.py          Detect factual claims in a draft response
                     (numeric + currency, date attribution, source
                     attribution). Pure regex/heuristic. Zero LLM.
  source_registry.py Authoritative source mappings ("share_price/TAL1T"
                     → nasdaqbaltic.com). Append-only JSON file.
  evidence.py        For each claim, query belief store and return a
                     DispatchDecision (ALLOW / ESCALATE / BLOCK).
  rewriter.py        Pure transformer: draft + decisions → final text.
  correction.py      Detect "actually it's X" patterns; consolidate
                     synchronously to belief store + Mem0 + source
                     registry; retract contradicting beliefs.
  pipeline.py        GroundingPipeline orchestrator — the public face.
  belief_adapter.py  Adapter interface so the pipeline is testable
                     without the real Phase 2 belief store.

Hot-path cost: ~0 LLM tokens (claim extraction + decision logic are
deterministic). Optional Tier-1 LLM disambiguation for ambiguous
correction patterns (~100 tokens, only when regex doesn't match).

The package is FEATURE-FLAGGED off by default. Activation by setting
SUBIA_GROUNDING_ENABLED=1 in the environment. When disabled, the
chat handler runs unchanged.
"""
from .claims import FactualClaim, ClaimKind, extract_claims
from .source_registry import SourceRegistry, RegisteredSource
from .evidence import (
    EvidenceCheck, GroundingDecision, GroundingVerdict,
    decide_for_claim,
)
from .rewriter import rewrite_response, RewriterResult
from .correction import (
    CorrectionCapture, DetectedCorrection,
)
from .belief_adapter import BeliefAdapter, InMemoryBeliefAdapter
from .pipeline import (
    GroundingPipeline, GroundingPipelineConfig, GroundingResult,
)

__all__ = [
    "FactualClaim", "ClaimKind", "extract_claims",
    "SourceRegistry", "RegisteredSource",
    "EvidenceCheck", "GroundingDecision", "GroundingVerdict",
    "decide_for_claim",
    "rewrite_response", "RewriterResult",
    "CorrectionCapture", "DetectedCorrection",
    "BeliefAdapter", "InMemoryBeliefAdapter",
    "GroundingPipeline", "GroundingPipelineConfig", "GroundingResult",
]
