"""Tests for app.trajectory.context_builder.

Verify:
  - Empty task returns ""
  - Retrieval failure returns "" (not raised)
  - Results-less retrieval returns ""
  - Tips are preferred over external-topic skills
  - fix_spiral prediction propagates to retrieve_task_conditional
  - Block wrapping with <trajectory_tips> tags
  - Character budget enforcement
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _result(text: str, score: float = 0.8, **meta):
    from app.retrieval.orchestrator import RetrievalResult
    return RetrievalResult(text=text, score=score, metadata=meta)


def test_empty_task_returns_empty():
    from app.trajectory.context_builder import compose_trajectory_hint_block
    out = compose_trajectory_hint_block(crew_name="research", task_text="")
    assert out == ""


def test_retrieve_failure_returns_empty():
    """When the orchestrator raises, caller gets "" — no exception."""
    from app.trajectory.context_builder import compose_trajectory_hint_block
    with patch("app.retrieval.orchestrator.RetrievalOrchestrator") as MockOrch:
        MockOrch.return_value.retrieve_task_conditional.side_effect = RuntimeError("boom")
        out = compose_trajectory_hint_block(
            crew_name="research", task_text="find x",
        )
    assert out == ""


def test_no_results_returns_empty():
    from app.trajectory.context_builder import compose_trajectory_hint_block
    with patch("app.retrieval.orchestrator.RetrievalOrchestrator") as MockOrch:
        MockOrch.return_value.retrieve_task_conditional.return_value = []
        out = compose_trajectory_hint_block(
            crew_name="research", task_text="find x",
        )
    assert out == ""


def test_block_wraps_tips_in_tag():
    from app.trajectory.context_builder import compose_trajectory_hint_block
    tips = [
        _result("pivot to cached path", score=0.9,
                tip_type="recovery", topic="Cache hit shortcut",
                agent_role="coding"),
        _result("always verify source", score=0.7,
                tip_type="strategy", topic="Verify cite",
                agent_role="research"),
    ]
    with patch("app.retrieval.orchestrator.RetrievalOrchestrator") as MockOrch:
        MockOrch.return_value.retrieve_task_conditional.return_value = tips
        out = compose_trajectory_hint_block(
            crew_name="research", task_text="a task",
        )
    assert out.startswith("<trajectory_tips>")
    assert out.endswith("</trajectory_tips>")
    assert "pivot to cached path" in out
    assert "always verify source" in out
    assert "(recovery" in out
    assert "(strategy" in out
    assert "Relevant prior learnings" in out


def test_external_skills_fallback_when_no_tips():
    """When retrieval returns only external-topic skills, surface top-1 as hint."""
    from app.trajectory.context_builder import compose_trajectory_hint_block
    externals = [
        _result("external skill content A", score=0.9, topic="A"),
        _result("external skill content B", score=0.85, topic="B"),
    ]
    with patch("app.retrieval.orchestrator.RetrievalOrchestrator") as MockOrch:
        MockOrch.return_value.retrieve_task_conditional.return_value = externals
        out = compose_trajectory_hint_block(
            crew_name="research", task_text="a task",
        )
    # Exactly one external surfaced
    assert "external skill content A" in out
    assert "external skill content B" not in out


def test_fix_spiral_propagates_to_orchestrator():
    """The predicted failure mode passes through to retrieve_task_conditional."""
    from app.trajectory.context_builder import compose_trajectory_hint_block
    with patch("app.retrieval.orchestrator.RetrievalOrchestrator") as MockOrch:
        mock_orch = MockOrch.return_value
        mock_orch.retrieve_task_conditional.return_value = []
        compose_trajectory_hint_block(
            crew_name="coding",
            task_text="broken fix loop",
            predicted_failure_mode="fix_spiral",
        )
    _, kwargs = mock_orch.retrieve_task_conditional.call_args
    assert kwargs["predicted_failure_mode"] == "fix_spiral"
    assert kwargs["agent_role"] == "coding"
    # Always filter to active records only
    assert kwargs["extra_where"] == {"status": "active"}


def test_block_enforces_character_cap():
    """Many large tips must not blow past the char budget."""
    from app.trajectory.context_builder import compose_trajectory_hint_block
    big_tip = "x" * 390  # within _TIP_EXCERPT_CAP = 400
    tips = [
        _result(big_tip, score=0.9 - i * 0.01, tip_type="strategy",
                topic=f"Tip {i}", agent_role="research")
        for i in range(30)
    ]
    with patch("app.retrieval.orchestrator.RetrievalOrchestrator") as MockOrch:
        MockOrch.return_value.retrieve_task_conditional.return_value = tips
        out = compose_trajectory_hint_block(
            crew_name="research", task_text="a task", top_k=30,
        )
    # _BLOCK_CHAR_CAP = 3000; allow a little headroom for closing tag
    assert len(out) <= 3200


def test_orchestrator_init_failure_returns_empty():
    from app.trajectory.context_builder import compose_trajectory_hint_block
    with patch("app.retrieval.orchestrator.RetrievalOrchestrator",
               side_effect=RuntimeError("init failed")):
        out = compose_trajectory_hint_block(
            crew_name="research", task_text="x",
        )
    assert out == ""
