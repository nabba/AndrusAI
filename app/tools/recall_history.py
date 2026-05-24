"""recall_history — agent tool for Gap 4 decade_recall retrieval.

Sibling to ``app.tools.recall_past_conversation`` (Q17.8). That tool
searches the operator's conversation history (audit.log). This one
searches the 6 hash-chained ledger files (continuity / changes /
drills / executor / agreement / governance) — the "arc of the
system" history.

Agents should call this when asked about historical decisions or
multi-year arcs: "what was the trajectory of X over the last 5
years?", "have we tried X before?", "when did the operator first
think about X?"
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


_VALID_SCOPES = {
    "continuity",
    "changes",
    "drills",
    "executor",
    "agreement",
    "governance",
}


def _format_references(query: str, refs: list, window_years: int) -> str:
    if not refs:
        return (
            f"No audit-history hit for {query!r} in the last "
            f"{window_years} years."
        )
    lines = [
        f"Audit history for {query!r} (window: {window_years}y, "
        f"top {len(refs)}):",
        "",
    ]
    for r in refs:
        d = r.to_dict() if hasattr(r, "to_dict") else r
        ts = (d.get("ts") or "")[:19]
        scope = d.get("scope") or "?"
        kind = d.get("kind") or "?"
        preview = (d.get("preview") or "")[:200]
        score = float(d.get("score") or 0.0)
        ref_id = d.get("ref") or "—"
        lines.append(
            f"  [{ts}] scope={scope} kind={kind} score={score:.2f} "
            f"ref={ref_id}\n      {preview}"
        )
    return "\n".join(lines)


def recall_history(
    query: str,
    *,
    scopes: list[str] | None = None,
    window_years: int = 10,
    top_k: int = 10,
) -> str:
    """Agent-callable entry. Filters by scope when provided."""
    try:
        from app.decade_recall.retrieval import recall_history as _rh
    except Exception as exc:
        return f"decade_recall unavailable: {type(exc).__name__}: {exc}"

    if scopes:
        invalid = set(scopes) - _VALID_SCOPES
        if invalid:
            return (
                f"recall_history: unknown scope(s) {sorted(invalid)!r}. "
                f"Valid scopes: {sorted(_VALID_SCOPES)!r}"
            )

    try:
        refs = _rh(
            query,
            scopes=scopes,
            window_years=window_years,
            top_k=top_k,
        )
    except Exception as exc:
        return f"Recall failed: {type(exc).__name__}: {exc}"
    return _format_references(query, refs, window_years)


try:
    from crewai.tools import BaseTool
    from pydantic import BaseModel, Field

    class _RecallHistorySchema(BaseModel):
        query: str = Field(description="Topic or keywords to search for.")
        scopes: list[str] | None = Field(
            default=None,
            description=(
                "Optional scope filter: any subset of "
                "{continuity, changes, drills, executor, agreement, "
                "governance}. None = all scopes."
            ),
        )
        window_years: int = Field(
            default=10, description="How far back to search, in years."
        )
        top_k: int = Field(
            default=10, description="Maximum number of references to return."
        )

    class RecallHistoryTool(BaseTool):
        name: str = "recall_history"
        description: str = (
            "Search the system's hash-chained audit history (continuity "
            "ledger, change requests, drill audit, executor runs, "
            "agreement ledger, governance amendments) for prior decisions "
            "or events matching a query. Returns up to top_k matching "
            "references with timestamps, scope, kind, preview. Use this "
            "for multi-year arc questions: 'when did X start?', "
            "'how often does Y happen?', 'have we decided on Z before?'."
        )
        args_schema: type = _RecallHistorySchema

        def _run(
            self,
            query: str,
            scopes: list[str] | None = None,
            window_years: int = 10,
            top_k: int = 10,
        ) -> str:
            return recall_history(
                query,
                scopes=scopes,
                window_years=window_years,
                top_k=top_k,
            )

    __all__ = ["RecallHistoryTool", "recall_history"]
except ImportError:
    __all__ = ["recall_history"]
