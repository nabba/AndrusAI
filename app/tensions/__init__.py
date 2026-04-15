"""
tensions — Contradiction / Paradox Knowledge Base.

Contains unresolved conflicts between principles, philosophy vs experience,
competing values, and open questions.  This is the dialectical growth engine:
growth comes from sitting with tension, not from premature resolution.

Most systems try to resolve contradictions.  A system pursuing genuine
growth should *hold* contradictions and revisit them.

Epistemic status: unresolved/dialectical.

IMMUTABLE — infrastructure-level module.
"""

from app.tensions.vectorstore import TensionStore, get_store
from app.tensions.tools import TensionSearchTool, RecordTensionTool, get_tension_tools

__all__ = [
    "TensionStore",
    "get_store",
    "TensionSearchTool",
    "RecordTensionTool",
    "get_tension_tools",
]
