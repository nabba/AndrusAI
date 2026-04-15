"""
experiential — Journal / Experiential Memory Knowledge Base.

Contains reflected experiences — the system's own narratives about what
happened and what it meant.  After significant interactions, the system
writes "journal entries" that become episodic memory.

This is the foundation for narrative identity: not just "what happened"
(operational memory), but "what did it mean to me" (experiential memory).

Epistemic status: subjective/phenomenological — true for the system,
not necessarily for the world.

IMMUTABLE — infrastructure-level module.
"""

from app.experiential.vectorstore import ExperientialStore, get_store
from app.experiential.tools import JournalSearchTool, get_experiential_tools

__all__ = [
    "ExperientialStore",
    "get_store",
    "JournalSearchTool",
    "get_experiential_tools",
]
