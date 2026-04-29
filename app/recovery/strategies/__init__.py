"""
recovery.strategies — concrete recovery attempts.

Each strategy module exposes ONE function::

    def execute(task: str, alt: Alternative, ctx: dict) -> StrategyResult

The recovery loop walks the librarian's ranked list, calling each
strategy in turn within a budget. The first one that returns a
``StrategyResult`` with success=True wins; the loop short-circuits
and delivers that text.

``ctx`` carries the original-call context the strategy may need:
  * ``commander``: bound Commander instance (for re_route)
  * ``user_input``: raw user prompt
  * ``crew_used``: the failed crew name
  * ``conversation_history``: optional history string
  * ``difficulty``: the original difficulty score
"""
from dataclasses import dataclass


@dataclass
class StrategyResult:
    success: bool
    text: str | None = None         # the recovered answer (when success)
    note: str | None = None         # short note for user ("redirected to PIM")
    route_changed: bool = False     # True if crew/tier/source materially changed
    error: str | None = None        # diagnostic when success=False
