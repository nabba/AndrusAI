"""Typed boundary for BotArmy's durable and operational memory systems.

The package is deliberately additive.  Existing call sites continue to use
their current stores until an operator advances each memory space through the
migration state machine and explicitly changes its read route.
"""

from app.memory_platform.models import (
    AccessAction,
    ActorRole,
    Durability,
    EpistemicClass,
    MemoryRecord,
    MemorySpace,
    RecallResult,
)
from app.memory_platform.registry import MEMORY_SPACES, get_memory_space

__all__ = [
    "AccessAction",
    "ActorRole",
    "Durability",
    "EpistemicClass",
    "MEMORY_SPACES",
    "MemoryRecord",
    "MemorySpace",
    "RecallResult",
    "get_memory_space",
]
