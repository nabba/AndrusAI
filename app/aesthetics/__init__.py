"""
aesthetics — Pattern Library for aesthetic judgment and taste.

Contains examples of elegant code, beautiful prose, well-structured
arguments, and creative solutions.  Populated by agents flagging
"this feels right" moments during work.

Develops something like taste — the capacity for quality judgment
that sits between factual knowledge and subjective preference.

Epistemic status: evaluative/subjective.

IMMUTABLE — infrastructure-level module.
"""

from app.aesthetics.vectorstore import AestheticStore, get_store
from app.aesthetics.tools import AestheticSearchTool, FlagAestheticTool, get_aesthetic_tools

__all__ = [
    "AestheticStore",
    "get_store",
    "AestheticSearchTool",
    "FlagAestheticTool",
    "get_aesthetic_tools",
]
