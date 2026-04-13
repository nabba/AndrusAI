"""subia.reverie — Phase 12 Proposal 1: Reverie Engine.

Mind-wandering during idle periods. Free-association walks across
ChromaDB wiki_pages + Neo4j relations + Mem0 full + fiction/philosophy
collections. Outputs speculative synthesis pages tagged
`epistemic_status: speculative` to `wiki/meta/reverie/`.

This package contains the orchestration logic. The actual graph walk
adapters and LLM caller are *injected* (constructor) so the engine
remains pure for testing and so production swaps are zero-friction.
The default `RealReverieAdapters` uses the existing wiki_tools and
Neo4j client; the test harness uses in-memory stubs.
"""
from .engine import (
    ReverieEngine,
    ReverieAdapters,
    ReverieResult,
)

__all__ = ["ReverieEngine", "ReverieAdapters", "ReverieResult"]
