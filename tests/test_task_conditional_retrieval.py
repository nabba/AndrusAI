"""Tests for RetrievalOrchestrator.retrieve_task_conditional.

Verify:
  - No filter conditions → delegates to retrieve() with where_filter=None
  - Single condition → single-key where clause
  - Multiple conditions → $and wrapped
  - fix_spiral auto-narrows to recovery tips (unless tip_types supplied)
  - extra_where merged correctly
  - Return type is list[RetrievalResult]
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_orchestrator():
    """Build an orchestrator without touching the thread pool — we mock retrieve()."""
    from app.retrieval.orchestrator import RetrievalOrchestrator
    from app.retrieval import config as cfg
    return RetrievalOrchestrator(cfg.RetrievalConfig())


def test_no_conditions_no_where_filter():
    orch = _make_orchestrator()
    with patch.object(orch, "retrieve", return_value=[]) as mock_retrieve:
        orch.retrieve_task_conditional(
            query="q", collections=["episteme_research"],
        )
    _, kwargs = mock_retrieve.call_args
    assert kwargs["where_filter"] is None


def test_agent_role_alone():
    orch = _make_orchestrator()
    with patch.object(orch, "retrieve", return_value=[]) as mock_retrieve:
        orch.retrieve_task_conditional(
            query="q", collections=["experiential_journal"],
            agent_role="coding",
        )
    _, kwargs = mock_retrieve.call_args
    assert kwargs["where_filter"] == {"agent_role": "coding"}


def test_tip_types_alone():
    orch = _make_orchestrator()
    with patch.object(orch, "retrieve", return_value=[]) as mock_retrieve:
        orch.retrieve_task_conditional(
            query="q", collections=["experiential_journal"],
            tip_types=["strategy", "recovery"],
        )
    _, kwargs = mock_retrieve.call_args
    assert kwargs["where_filter"] == {"tip_type": {"$in": ["strategy", "recovery"]}}


def test_combined_conditions_use_and():
    orch = _make_orchestrator()
    with patch.object(orch, "retrieve", return_value=[]) as mock_retrieve:
        orch.retrieve_task_conditional(
            query="q", collections=["episteme_research"],
            agent_role="research", tip_types=["optimization"],
        )
    _, kwargs = mock_retrieve.call_args
    wf = kwargs["where_filter"]
    assert "$and" in wf
    assert {"tip_type": {"$in": ["optimization"]}} in wf["$and"]
    assert {"agent_role": "research"} in wf["$and"]


def test_fix_spiral_auto_narrows_to_recovery():
    """When Observer predicts fix_spiral and caller doesn't pin tip_types,
    the filter narrows to recovery tips."""
    orch = _make_orchestrator()
    with patch.object(orch, "retrieve", return_value=[]) as mock_retrieve:
        orch.retrieve_task_conditional(
            query="q", collections=["unresolved_tensions"],
            predicted_failure_mode="fix_spiral",
        )
    _, kwargs = mock_retrieve.call_args
    assert kwargs["where_filter"] == {"tip_type": {"$in": ["recovery"]}}


def test_explicit_tip_types_wins_over_prediction():
    """If the caller pins tip_types, the prediction heuristic defers."""
    orch = _make_orchestrator()
    with patch.object(orch, "retrieve", return_value=[]) as mock_retrieve:
        orch.retrieve_task_conditional(
            query="q", collections=["experiential_journal"],
            predicted_failure_mode="fix_spiral",
            tip_types=["strategy"],
        )
    _, kwargs = mock_retrieve.call_args
    assert kwargs["where_filter"] == {"tip_type": {"$in": ["strategy"]}}


def test_non_fix_spiral_prediction_does_not_narrow():
    """Other predicted failure modes don't narrow — they could be used
    for custom future heuristics."""
    orch = _make_orchestrator()
    with patch.object(orch, "retrieve", return_value=[]) as mock_retrieve:
        orch.retrieve_task_conditional(
            query="q", collections=["episteme_research"],
            predicted_failure_mode="scope_creep",
        )
    _, kwargs = mock_retrieve.call_args
    # No conditions active → where_filter is None
    assert kwargs["where_filter"] is None


def test_extra_where_merged_with_and():
    """Caller-supplied extra_where combines with constructed filter via $and."""
    orch = _make_orchestrator()
    with patch.object(orch, "retrieve", return_value=[]) as mock_retrieve:
        orch.retrieve_task_conditional(
            query="q", collections=["experiential_journal"],
            tip_types=["recovery"],
            extra_where={"status": "active"},
        )
    _, kwargs = mock_retrieve.call_args
    wf = kwargs["where_filter"]
    assert "$and" in wf
    assert {"tip_type": {"$in": ["recovery"]}} in wf["$and"]
    assert {"status": "active"} in wf["$and"]


def test_extra_where_alone():
    """When the only condition is extra_where, no redundant $and wrapper."""
    orch = _make_orchestrator()
    with patch.object(orch, "retrieve", return_value=[]) as mock_retrieve:
        orch.retrieve_task_conditional(
            query="q", collections=["episteme_research"],
            extra_where={"status": "active"},
        )
    _, kwargs = mock_retrieve.call_args
    assert kwargs["where_filter"] == {"status": "active"}


def test_top_k_and_task_id_pass_through():
    orch = _make_orchestrator()
    with patch.object(orch, "retrieve", return_value=[]) as mock_retrieve:
        orch.retrieve_task_conditional(
            query="q", collections=["episteme_research"],
            top_k=7, task_id="task_42",
        )
    _, kwargs = mock_retrieve.call_args
    assert kwargs["top_k"] == 7
    assert kwargs["task_id"] == "task_42"


def test_existing_retrieve_interface_unchanged():
    """The existing retrieve() signature must be unchanged — callers who
    don't use retrieve_task_conditional see zero difference."""
    from app.retrieval.orchestrator import RetrievalOrchestrator
    import inspect
    sig = inspect.signature(RetrievalOrchestrator.retrieve)
    params = list(sig.parameters.keys())
    # Same keyword args as before our change (self, query, collections,
    # top_k, where_filter, min_score, task_id) — no new required args.
    assert "query" in params
    assert "collections" in params
    assert "top_k" in params
    assert "where_filter" in params
    assert "min_score" in params
    assert "task_id" in params
