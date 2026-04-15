"""
episteme — Metacognitive / Research Knowledge Base.

Contains research papers, architecture decisions, design patterns, and
failed experiments.  Grounds the Self-Improver in theory so it makes
principled improvements, not just hill-climbing.

Epistemic status: theoretical/empirical — more trustworthy than fiction,
but still claims that need validation against the specific system.

IMMUTABLE — infrastructure-level module.
"""

from app.episteme.vectorstore import EpistemeStore, get_store
from app.episteme.tools import EpistemeSearchTool, get_episteme_tools

__all__ = [
    "EpistemeStore",
    "get_store",
    "EpistemeSearchTool",
    "get_episteme_tools",
]
