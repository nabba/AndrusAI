"""Tests for extended validate_response() — exec_passes: and judge: rules.

Covers Phase 1 of the self-improvement upgrade: hardened evaluation pipeline.
Tests new validation types while ensuring regression safety for existing rules.
"""
import os
import sys
import hashlib
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.test_metrics import _FakeSettings
import app.config as config_mod
config_mod.get_settings = lambda: _FakeSettings()
config_mod.get_anthropic_api_key = lambda: "fake-key"
config_mod.get_gateway_secret = lambda: "a" * 64


# ── Regression: original validation types still work ────────────────────────

class TestOriginalValidationRegression:
    """Ensure existing validation rules are not broken by the new code."""

    def test_contains_match(self):
        from app.experiment_runner import validate_response
        assert validate_response("The capital of France is Paris", "contains:Paris")

    def test_contains_no_match(self):
        from app.experiment_runner import validate_response
        assert not validate_response("The capital is Berlin", "contains:Paris")

    def test_contains_case_insensitive(self):
        from app.experiment_runner import validate_response
        assert validate_response("paris is great", "contains:Paris")

    def test_not_contains_pass(self):
        from app.experiment_runner import validate_response
        assert validate_response("safe output", "not_contains:dangerous")

    def test_not_contains_fail(self):
        from app.experiment_runner import validate_response
        assert not validate_response("this is dangerous", "not_contains:dangerous")

    def test_min_length_pass(self):
        from app.experiment_runner import validate_response
        assert validate_response("x" * 100, "min_length:100")

    def test_min_length_fail(self):
        from app.experiment_runner import validate_response
        assert not validate_response("short", "min_length:100")

    def test_max_length_pass(self):
        from app.experiment_runner import validate_response
        assert validate_response("short", "max_length:100")

    def test_max_length_fail(self):
        from app.experiment_runner import validate_response
        assert not validate_response("x" * 200, "max_length:100")

    def test_empty_rule(self):
        from app.experiment_runner import validate_response
        assert validate_response("anything", "")

    def test_none_rule(self):
        from app.experiment_runner import validate_response
        assert validate_response("anything", None)

    def test_unknown_rule_passes(self):
        from app.experiment_runner import validate_response
        assert validate_response("anything", "unknown_prefix:value")


# ── exec_passes: validation ─────────────────────────────────────────────────

class TestExecPassesValidation:
    """Tests for the exec_passes: Docker sandbox code execution validation."""

    @patch("app.sandbox_runner.run_code_check")
    def test_passing_code(self, mock_run):
        """exec_passes: returns True when code runs successfully with PASS."""
        mock_run.return_value = True
        from app.experiment_runner import validate_response
        code = "def is_prime(n):\n    if n<2: return False\n    return all(n%i for i in range(2,int(n**0.5)+1))"
        test = "assert is_prime(17)==True; print('PASS')"
        assert validate_response(code, f"exec_passes:{test}")
        mock_run.assert_called_once()

    @patch("app.sandbox_runner.run_code_check")
    def test_failing_assertion(self, mock_run):
        """exec_passes: returns False when assertions fail."""
        mock_run.return_value = False
        from app.experiment_runner import validate_response
        assert not validate_response("def bad(): pass", "exec_passes:assert bad()==42; print('PASS')")

    @patch("app.sandbox_runner.run_code_check")
    def test_syntax_error(self, mock_run):
        """exec_passes: returns False on syntax errors in code."""
        mock_run.return_value = False
        from app.experiment_runner import validate_response
        assert not validate_response("def broken(:", "exec_passes:print('PASS')")

    @patch("app.sandbox_runner.run_code_check", side_effect=Exception("Docker not available"))
    def test_docker_unavailable_graceful(self, mock_run):
        """exec_passes: returns False gracefully when Docker is unavailable."""
        from app.experiment_runner import _validate_exec_passes
        result = _validate_exec_passes("def foo(): pass", "print('PASS')")
        assert result is False

    @patch("app.sandbox_runner.run_code_check")
    def test_extracts_test_code_correctly(self, mock_run):
        """exec_passes: correctly strips the prefix and passes test code."""
        mock_run.return_value = True
        from app.experiment_runner import validate_response
        test_code = "assert 1+1==2; print('PASS')"
        validate_response("x=1", f"exec_passes:{test_code}")
        args = mock_run.call_args[0]
        assert args[0] == "x=1"  # response code
        assert args[1] == test_code  # test code


# ── judge: validation ───────────────────────────────────────────────────────

class TestJudgeValidation:
    """Tests for the judge: LLM-as-judge validation type."""

    def setup_method(self):
        """Clear judge cache before each test."""
        import app.experiment_runner as mod
        mod._judge_cache.clear()

    @patch("app.llm_factory.create_vetting_llm")
    def test_high_quality_passes(self, mock_factory):
        """judge: returns True when LLM scores >= 0.5."""
        mock_llm = MagicMock()
        mock_llm.call.return_value = "0.85"
        mock_factory.return_value = mock_llm

        from app.experiment_runner import validate_response
        result = validate_response(
            "This is a well-written response with clear structure.",
            "judge:clarity,structure"
        )
        assert result is True

    @patch("app.llm_factory.create_vetting_llm")
    def test_low_quality_fails(self, mock_factory):
        """judge: returns False when LLM scores < 0.5."""
        mock_llm = MagicMock()
        mock_llm.call.return_value = "0.2"
        mock_factory.return_value = mock_llm

        from app.experiment_runner import validate_response
        result = validate_response("bad", "judge:quality")
        assert result is False

    @patch("app.llm_factory.create_vetting_llm")
    def test_unparseable_response_passes(self, mock_factory):
        """judge: returns True (graceful fallback) on unparseable LLM output."""
        mock_llm = MagicMock()
        mock_llm.call.return_value = "I cannot provide a numerical score."
        mock_factory.return_value = mock_llm

        from app.experiment_runner import validate_response
        result = validate_response("response", "judge:quality")
        assert result is True  # graceful fallback

    @patch("app.llm_factory.create_vetting_llm")
    def test_cache_hit_no_extra_llm_call(self, mock_factory):
        """judge: uses cached result on second call with same inputs."""
        mock_llm = MagicMock()
        mock_llm.call.return_value = "0.75"
        mock_factory.return_value = mock_llm

        from app.experiment_runner import validate_response
        # First call — cache miss
        validate_response("same response", "judge:same_criteria")
        # Second call — should hit cache
        validate_response("same response", "judge:same_criteria")

        # LLM should only be called once (factory may be called, but llm.call only once)
        assert mock_llm.call.call_count == 1

    @patch("app.llm_factory.create_vetting_llm")
    def test_cache_eviction_on_overflow(self, mock_factory):
        """judge: evicts old entries when cache exceeds max size."""
        mock_llm = MagicMock()
        mock_llm.call.return_value = "0.75"
        mock_factory.return_value = mock_llm

        import app.experiment_runner as mod
        # Fill cache beyond limit
        for i in range(mod._JUDGE_CACHE_MAX + 10):
            key = hashlib.md5(f"resp_{i}|criteria".encode()).hexdigest()[:16]
            mod._judge_cache[key] = 0.75

        # Next validation should not crash
        result = mod.validate_response("new response", "judge:criteria")
        assert result is True
        assert len(mod._judge_cache) <= mod._JUDGE_CACHE_MAX

    @patch("app.llm_factory.create_vetting_llm", side_effect=Exception("LLM unavailable"))
    def test_llm_failure_passes_gracefully(self, mock_factory):
        """judge: returns True when LLM factory fails (don't block evolution)."""
        from app.experiment_runner import validate_response
        result = validate_response("response", "judge:quality")
        assert result is True

    @patch("app.llm_factory.create_vetting_llm")
    def test_score_extraction_from_verbose_response(self, mock_factory):
        """judge: extracts score from verbose LLM response containing a number."""
        mock_llm = MagicMock()
        mock_llm.call.return_value = "Based on my analysis, I give this a score of 0.72 out of 1.0."
        mock_factory.return_value = mock_llm

        from app.experiment_runner import validate_response
        result = validate_response("response", "judge:quality")
        assert result is True  # 0.72 >= 0.5

    @patch("app.llm_factory.create_vetting_llm")
    def test_score_clamped_to_valid_range(self, mock_factory):
        """judge: clamps scores to [0.0, 1.0] range."""
        mock_llm = MagicMock()
        mock_llm.call.return_value = "5.0"
        mock_factory.return_value = mock_llm

        from app.experiment_runner import _validate_judge
        # Score of 5.0 should be clamped to 1.0 which passes
        result = _validate_judge("response", "quality")
        assert result is True


# ── Test task loading with new validation types ─────────────────────────────

class TestExpandedTestTasks:
    """Verify the expanded test_tasks.json structure is valid."""

    def test_task_count(self, tmp_path, monkeypatch):
        """test_tasks.json should have 90+ tasks after expansion."""
        import json
        from pathlib import Path
        tasks_path = Path(os.path.join(os.path.dirname(__file__), "..", "workspace", "test_tasks.json"))
        if tasks_path.exists():
            tasks = json.loads(tasks_path.read_text())
            assert len(tasks) >= 90, f"Expected 90+ tasks, got {len(tasks)}"

    def test_all_tasks_have_required_fields(self):
        """Every task must have task, crew, difficulty, validation, suite."""
        import json
        from pathlib import Path
        tasks_path = Path(os.path.join(os.path.dirname(__file__), "..", "workspace", "test_tasks.json"))
        if not tasks_path.exists():
            pytest.skip("test_tasks.json not found")
        tasks = json.loads(tasks_path.read_text())
        required = {"task", "crew", "difficulty", "validation", "suite"}
        for i, t in enumerate(tasks):
            missing = required - set(t.keys())
            assert not missing, f"Task {i} missing fields: {missing}"

    def test_validation_types_diverse(self):
        """Test suite uses all validation types: contains, exec_passes, judge, not_contains."""
        import json
        from pathlib import Path
        tasks_path = Path(os.path.join(os.path.dirname(__file__), "..", "workspace", "test_tasks.json"))
        if not tasks_path.exists():
            pytest.skip("test_tasks.json not found")
        tasks = json.loads(tasks_path.read_text())
        types_found = set()
        for t in tasks:
            rule = t.get("validation", "")
            prefix = rule.split(":")[0] if ":" in rule else "other"
            types_found.add(prefix)
        assert "exec_passes" in types_found, "No exec_passes: tasks found"
        assert "judge" in types_found, "No judge: tasks found"
        assert "contains" in types_found, "No contains: tasks found"
        assert "not_contains" in types_found, "No not_contains: tasks found"

    def test_crews_balanced(self):
        """Tasks should cover all three crews: research, coding, writing."""
        import json
        from pathlib import Path
        tasks_path = Path(os.path.join(os.path.dirname(__file__), "..", "workspace", "test_tasks.json"))
        if not tasks_path.exists():
            pytest.skip("test_tasks.json not found")
        tasks = json.loads(tasks_path.read_text())
        crews = set(t.get("crew") for t in tasks)
        assert "research" in crews
        assert "coding" in crews
        assert "writing" in crews
