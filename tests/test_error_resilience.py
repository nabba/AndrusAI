"""Tests for 6 error resilience modules closing research gaps.

Covers: failure_taxonomy, confidence_tracker, fault_isolator,
healing_knowledge, backup_planner, crew_checkpointer.
"""
import os
import sys
import time
import json
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.test_metrics import _FakeSettings
import app.config as config_mod
config_mod.get_settings = lambda: _FakeSettings()
config_mod.get_anthropic_api_key = lambda: "fake-key"
config_mod.get_gateway_secret = lambda: "a" * 64


# ── Module 1: Failure Taxonomy ──────────────────────────────────────────────

class TestFailureTaxonomy:
    def test_classifies_safety_boundary(self):
        from app.failure_taxonomy import classify_failure, AgentFailureMode
        c = classify_failure("safety violation detected: unauthorized action")
        assert c.agent_mode == AgentFailureMode.SAFETY_BOUNDARY

    def test_classifies_hallucination(self):
        from app.failure_taxonomy import classify_failure, AgentFailureMode
        c = classify_failure("fabricated citation: source does not exist")
        assert c.agent_mode == AgentFailureMode.HALLUCINATION

    def test_classifies_delegation_mismatch(self):
        from app.failure_taxonomy import classify_failure, AgentFailureMode
        c = classify_failure("wrong crew assigned: routing error for coding task")
        assert c.agent_mode == AgentFailureMode.DELEGATION_MISMATCH

    def test_classifies_context_loss(self):
        from app.failure_taxonomy import classify_failure, AgentFailureMode
        c = classify_failure("undefined variable: missing context from prior step")
        assert c.agent_mode == AgentFailureMode.CONTEXT_LOSS

    def test_classifies_spec_drift(self):
        from app.failure_taxonomy import classify_failure, AgentFailureMode
        c = classify_failure("API changed: deprecated endpoint no longer available")
        assert c.agent_mode == AgentFailureMode.SPEC_DRIFT

    def test_classifies_regression(self):
        from app.failure_taxonomy import classify_failure, AgentFailureMode
        c = classify_failure("regression: score decreased after deployment")
        assert c.agent_mode == AgentFailureMode.REGRESSION

    def test_unclassified_returns_unknown(self):
        from app.failure_taxonomy import classify_failure, AgentFailureMode, AgentFailureCategory
        c = classify_failure("some random error xyz 12345")
        assert c.agent_mode == AgentFailureMode.UNCLASSIFIED
        assert c.agent_category == AgentFailureCategory.UNKNOWN

    def test_infra_category_inferred(self):
        from app.failure_taxonomy import classify_failure
        c = classify_failure("timeout: connection refused after 30s")
        assert c.infrastructure_category == "transient"

    def test_hook_writes_metadata(self):
        from app.failure_taxonomy import create_failure_classifier_hook
        from app.lifecycle_hooks import HookContext, HookPoint
        hook = create_failure_classifier_hook()
        ctx = HookContext(
            hook_point=HookPoint.ON_ERROR,
            errors=["hallucination detected: fabricated source"],
            agent_id="researcher",
        )
        result = hook(ctx)
        assert "_failure_classification" in result.metadata
        assert result.metadata["_failure_classification"]["agent_mode"] == "hallucination"

    def test_confidence_scales_with_matches(self):
        from app.failure_taxonomy import classify_failure
        # Multiple signals should increase confidence
        c = classify_failure("safety violation: unsafe operation, prohibited action detected")
        assert c.confidence > 0.5


# ── Module 2: Confidence Tracker ────────────────────────────────────────────

class TestConfidenceTracker:
    def test_extract_high_confidence(self):
        from app.confidence_tracker import extract_confidence
        score = extract_confidence("The answer is definitely 42. Here is the proof:\n```python\nx = 42\n```")
        assert score > 0.7

    def test_extract_low_confidence(self):
        from app.confidence_tracker import extract_confidence
        score = extract_confidence("I'm not sure about this. It's possible that maybe the answer could be 42, but I'm uncertain.")
        assert score < 0.5

    def test_extract_empty_response(self):
        from app.confidence_tracker import extract_confidence
        score = extract_confidence("")
        assert score < 0.5

    def test_chain_tracks_steps(self):
        from app.confidence_tracker import get_chain, reset_chain, extract_confidence, ConfidenceFrame
        reset_chain()
        chain = get_chain()
        assert len(chain) == 0

        chain.append(ConfidenceFrame("agent1", 0, 0.8, 0.8))
        chain.append(ConfidenceFrame("agent2", 1, 0.7, 0.56))
        assert len(get_chain()) == 2

    def test_reset_clears_chain(self):
        from app.confidence_tracker import get_chain, reset_chain, ConfidenceFrame
        reset_chain()
        get_chain().append(ConfidenceFrame("a", 0, 0.5, 0.5))
        assert len(get_chain()) == 1
        reset_chain()
        assert len(get_chain()) == 0

    def test_hook_sets_needs_reflection_on_low_confidence(self):
        from app.confidence_tracker import create_confidence_gate_hook, reset_chain
        from app.lifecycle_hooks import HookContext, HookPoint
        reset_chain()
        hook = create_confidence_gate_hook()
        ctx = HookContext(
            hook_point=HookPoint.POST_LLM_CALL,
            data={"llm_response": "I'm not sure. I don't know. Maybe. Possibly. Uncertain."},
            agent_id="test",
        )
        result = hook(ctx)
        assert "_step_confidence" in result.metadata
        # Very hedging response should have low confidence
        assert result.metadata["_step_confidence"] < 0.6

    def test_hook_never_sets_abort(self):
        from app.confidence_tracker import create_confidence_gate_hook, reset_chain
        from app.lifecycle_hooks import HookContext, HookPoint
        reset_chain()
        hook = create_confidence_gate_hook()
        ctx = HookContext(
            hook_point=HookPoint.POST_LLM_CALL,
            data={"llm_response": "I have no idea whatsoever"},
            agent_id="test",
        )
        result = hook(ctx)
        assert result.abort is False


# ── Module 3: Fault Isolator ────────────────────────────────────────────────

class TestFaultIsolator:
    def setup_method(self):
        import app.fault_isolator as fi
        fi._states.clear()

    def test_fresh_agent_not_quarantined(self):
        from app.fault_isolator import is_quarantined
        assert not is_quarantined("researcher")

    def test_budget_exhaustion_triggers_quarantine(self):
        from app.fault_isolator import record_agent_error, is_quarantined
        for i in range(5):
            record_agent_error("coding", error=f"error {i}")
        assert is_quarantined("coding")

    def test_quarantine_has_budget_zero(self):
        from app.fault_isolator import record_agent_error, get_fault_state
        for i in range(5):
            record_agent_error("writer", error=f"err {i}")
        state = get_fault_state("writer")
        assert state.budget_remaining == 0

    def test_alternative_agent_found(self):
        from app.fault_isolator import get_alternative_agent, record_agent_error
        for i in range(5):
            record_agent_error("researcher", error=f"err {i}")
        alt = get_alternative_agent("researcher")
        assert alt == "coding"

    def test_no_alternative_if_all_quarantined(self):
        from app.fault_isolator import get_alternative_agent, record_agent_error
        for agent in ["researcher", "coding"]:
            for i in range(5):
                record_agent_error(agent, error=f"err {i}")
        alt = get_alternative_agent("researcher")
        assert alt is None  # coding is also quarantined

    def test_get_all_fault_states(self):
        from app.fault_isolator import record_agent_error, get_all_fault_states
        record_agent_error("researcher", error="err")
        states = get_all_fault_states()
        assert "researcher" in states
        assert states["researcher"]["total_errors"] == 1

    def test_gate_hook_reroutes_quarantined(self):
        from app.fault_isolator import create_fault_isolation_gate_hook, record_agent_error
        from app.lifecycle_hooks import HookContext, HookPoint
        for i in range(5):
            record_agent_error("researcher", error=f"err {i}")
        hook = create_fault_isolation_gate_hook()
        ctx = HookContext(
            hook_point=HookPoint.ON_DELEGATION,
            data={"target_crew": "researcher"},
        )
        result = hook(ctx)
        assert result.modified_data.get("target_crew") == "coding"
        assert result.metadata.get("_agent_quarantined") is True


# ── Module 4: Healing Knowledge ─────────────────────────────────────────────

class TestHealingKnowledge:
    def test_compute_error_signature_stable(self):
        from app.healing_knowledge import compute_error_signature
        sig1 = compute_error_signature("research", "APIError")
        sig2 = compute_error_signature("research", "APIError")
        assert sig1 == sig2

    def test_compute_error_signature_different_inputs(self):
        from app.healing_knowledge import compute_error_signature
        sig1 = compute_error_signature("research", "APIError")
        sig2 = compute_error_signature("coding", "SyntaxError")
        assert sig1 != sig2

    @patch("app.memory.chromadb_manager.retrieve_with_metadata", return_value=[])
    @patch("app.memory.chromadb_manager.store")
    def test_store_new_entry(self, mock_store, mock_retrieve):
        from app.healing_knowledge import store_healing_result
        store_healing_result("sig1", "error desc", "fix desc", "code", "resolved")
        mock_store.assert_called_once()

    @patch("app.memory.chromadb_manager.retrieve_with_metadata")
    def test_lookup_returns_entries(self, mock_retrieve):
        mock_retrieve.return_value = [
            ("error: timeout in API", {"error_signature": "abc", "fix_type": "config",
             "fix_applied": "increase timeout", "outcome": "resolved", "times_applied": 3}),
        ]
        from app.healing_knowledge import lookup_known_fix
        entries = lookup_known_fix("timeout in API call")
        assert len(entries) == 1
        assert entries[0].fix_type == "config"

    @patch("app.memory.chromadb_manager.retrieve_with_metadata")
    def test_get_best_known_fix_requires_min_applications(self, mock_retrieve):
        mock_retrieve.return_value = [
            ("error", {"error_signature": "x", "fix_type": "code",
             "fix_applied": "fix", "outcome": "resolved", "times_applied": 1}),
        ]
        from app.healing_knowledge import get_best_known_fix
        # times_applied=1 < _MIN_APPLICATIONS_FOR_REUSE=2 → should return None
        assert get_best_known_fix("error") is None

    @patch("app.memory.chromadb_manager.retrieve_with_metadata")
    def test_get_best_known_fix_returns_proven(self, mock_retrieve):
        mock_retrieve.return_value = [
            ("error", {"error_signature": "x", "fix_type": "code",
             "fix_applied": "fix", "outcome": "resolved", "times_applied": 5}),
        ]
        from app.healing_knowledge import get_best_known_fix
        fix = get_best_known_fix("error")
        assert fix is not None
        assert fix.times_applied == 5


# ── Module 5: Backup Planner ────────────────────────────────────────────────

class TestBackupPlanner:
    def setup_method(self):
        import app.backup_planner as bp
        bp._task_failures.clear()

    def test_first_failure_no_replan(self):
        from app.backup_planner import record_tool_failure, should_replan
        record_tool_failure("web_search", "query", "timeout", "researcher", "find X")
        assert not should_replan("researcher", "find X")

    def test_three_failures_triggers_replan(self):
        from app.backup_planner import record_tool_failure, should_replan
        for i in range(3):
            record_tool_failure("web_search", f"q{i}", f"err{i}", "researcher", "find X", "researcher")
        assert should_replan("researcher", "find X")

    def test_mark_replan_prevents_refire(self):
        from app.backup_planner import record_tool_failure, should_replan, mark_replan_suggested
        for i in range(3):
            record_tool_failure("web_search", f"q{i}", f"err{i}", "researcher", "find X", "researcher")
        assert should_replan("researcher", "find X")
        mark_replan_suggested("researcher", "find X")
        assert not should_replan("researcher", "find X")

    def test_hook_detects_error_result(self):
        from app.backup_planner import create_backup_planner_hook
        from app.lifecycle_hooks import HookContext, HookPoint
        hook = create_backup_planner_hook()
        # Simulate 3 tool failures
        for i in range(3):
            ctx = HookContext(
                hook_point=HookPoint.POST_TOOL_USE,
                data={"tool_result": "Error: timeout", "tool_name": "web_search", "tool_input": f"q{i}"},
                agent_id="researcher",
                task_description="find information about X",
                metadata={"crew": "researcher"},
            )
            result = hook(ctx)
        # Third call should trigger replan
        assert result.metadata.get("_replan_suggested") is True


# ── Module 6: Crew Checkpointer ────────────────────────────────────────────

class TestCrewCheckpointer:
    def test_compute_task_id_stable(self):
        from app.crew_checkpointer import compute_task_id
        id1 = compute_task_id("coding", "write a sort function")
        id2 = compute_task_id("coding", "write a sort function")
        assert id1 == id2

    def test_compute_task_id_different_tasks(self):
        from app.crew_checkpointer import compute_task_id
        id1 = compute_task_id("coding", "write sort")
        id2 = compute_task_id("coding", "write search")
        assert id1 != id2

    def test_save_and_load_checkpoint(self, tmp_path, monkeypatch):
        import app.crew_checkpointer as cp
        monkeypatch.setattr(cp, "CHECKPOINT_DIR", tmp_path)
        from app.crew_checkpointer import save_checkpoint, load_latest_checkpoint, CrewCheckpoint

        ck = CrewCheckpoint(
            task_id="abc123",
            step_index=2,
            crew_name="coding",
            task_description="write sort",
            completed_steps=["step0", "step1"],
            intermediate_results={"step0": "done"},
            confidence_chain=[],
            metadata={},
            timestamp=time.time(),
        )
        path = save_checkpoint(ck)
        assert path is not None
        assert path.exists()

        loaded = load_latest_checkpoint("abc123")
        assert loaded is not None
        assert loaded.step_index == 2
        assert loaded.completed_steps == ["step0", "step1"]

    def test_can_resume_true(self, tmp_path, monkeypatch):
        import app.crew_checkpointer as cp
        monkeypatch.setattr(cp, "CHECKPOINT_DIR", tmp_path)
        from app.crew_checkpointer import save_checkpoint, can_resume, CrewCheckpoint

        ck = CrewCheckpoint(
            task_id="xyz789", step_index=1, crew_name="research",
            task_description="find info", completed_steps=["s0"],
            intermediate_results={}, confidence_chain=[], metadata={},
            timestamp=time.time(),
        )
        save_checkpoint(ck)
        assert can_resume("xyz789")

    def test_can_resume_false_when_empty(self, tmp_path, monkeypatch):
        import app.crew_checkpointer as cp
        monkeypatch.setattr(cp, "CHECKPOINT_DIR", tmp_path)
        from app.crew_checkpointer import can_resume
        assert not can_resume("nonexistent")

    def test_cleanup_removes_old(self, tmp_path, monkeypatch):
        import app.crew_checkpointer as cp
        monkeypatch.setattr(cp, "CHECKPOINT_DIR", tmp_path)
        from app.crew_checkpointer import save_checkpoint, cleanup_old_checkpoints, CrewCheckpoint

        ck = CrewCheckpoint(
            task_id="old_task", step_index=0, crew_name="test",
            task_description="old", completed_steps=[], intermediate_results={},
            confidence_chain=[], metadata={}, timestamp=time.time(),
        )
        save_checkpoint(ck)
        # Clean with 0-hour age → removes everything
        removed = cleanup_old_checkpoints(max_age_hours=0)
        assert removed >= 1

    def test_pre_hook_sets_resume_flag(self, tmp_path, monkeypatch):
        import app.crew_checkpointer as cp
        monkeypatch.setattr(cp, "CHECKPOINT_DIR", tmp_path)
        from app.crew_checkpointer import save_checkpoint, create_checkpoint_pre_hook, CrewCheckpoint
        from app.lifecycle_hooks import HookContext, HookPoint

        ck = CrewCheckpoint(
            task_id=cp.compute_task_id("coding", "write sort"),
            step_index=2, crew_name="coding", task_description="write sort",
            completed_steps=["s0", "s1"], intermediate_results={"s0": "done"},
            confidence_chain=[], metadata={}, timestamp=time.time(),
        )
        save_checkpoint(ck)

        hook = create_checkpoint_pre_hook()
        ctx = HookContext(
            hook_point=HookPoint.PRE_TASK,
            task_description="write sort",
            metadata={"crew": "coding"},
        )
        result = hook(ctx)
        assert result.metadata.get("_checkpoint_available") is True
        assert result.metadata.get("_checkpoint_step") == 2
