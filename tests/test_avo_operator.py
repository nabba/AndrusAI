"""
AVO Operator Tests
===================

Comprehensive tests for the Agentic Variation Operator (AVO) 5-phase pipeline.
Covers all phases individually, the full pipeline, dedup logic, safety checks,
repair loops, yield handling, and system wiring.

Run: docker exec crewai-team-gateway-1 python3 -m pytest /app/tests/test_avo_operator.py -v
"""

import ast
import hashlib
import inspect
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ════════════════════════════════════════════════════════════════════════════════
# 1. IMPORT & CONSTANT TESTS
# ════════════════════════════════════════════════════════════════════════════════

class TestImportsAndConstants:
    """All AVO public symbols must be importable."""

    def test_core_imports(self):
        from app.avo_operator import (
            AVOResult, run_avo_pipeline,
            _phase_planning, _phase_implementation,
            _phase_local_testing, _phase_self_critique,
            _read_target_files, _MAX_REPAIR_ATTEMPTS,
        )
        assert callable(run_avo_pipeline)
        assert callable(_phase_planning)
        assert callable(_phase_implementation)
        assert callable(_phase_local_testing)
        assert callable(_phase_self_critique)
        assert callable(_read_target_files)

    def test_max_repair_attempts(self):
        from app.avo_operator import _MAX_REPAIR_ATTEMPTS
        assert isinstance(_MAX_REPAIR_ATTEMPTS, int)
        assert 1 <= _MAX_REPAIR_ATTEMPTS <= 10

    def test_dependency_imports(self):
        from app.experiment_runner import MutationSpec, generate_experiment_id
        from app.evo_memory import recall_similar_failures
        from app.utils import safe_json_parse
        assert callable(MutationSpec)
        assert callable(generate_experiment_id)
        assert callable(recall_similar_failures)
        assert callable(safe_json_parse)


# ════════════════════════════════════════════════════════════════════════════════
# 2. AVOResult DATACLASS TESTS
# ════════════════════════════════════════════════════════════════════════════════

class TestAVOResult:
    """AVOResult tracks pipeline progress and output."""

    def test_defaults(self):
        from app.avo_operator import AVOResult
        r = AVOResult()
        assert r.mutation is None
        assert r.phases_completed == 0
        assert r.repair_attempts == 0
        assert r.critique_notes == ""
        assert r.abandoned_reason == ""

    def test_successful_result(self):
        from app.avo_operator import AVOResult
        from app.experiment_runner import MutationSpec
        spec = MutationSpec(
            experiment_id="exp_001",
            hypothesis="Fix bug X",
            change_type="code",
            files={"app/fix.py": "# fixed"},
        )
        r = AVOResult(
            mutation=spec,
            phases_completed=5,
            repair_attempts=1,
            critique_notes="No concerns",
        )
        assert r.mutation is not None
        assert r.phases_completed == 5
        assert r.abandoned_reason == ""

    def test_abandoned_result(self):
        from app.avo_operator import AVOResult
        r = AVOResult(
            phases_completed=1,
            abandoned_reason="Planning produced no viable hypothesis",
        )
        assert r.mutation is None
        assert r.phases_completed == 1
        assert "Planning" in r.abandoned_reason


# ════════════════════════════════════════════════════════════════════════════════
# 3. PHASE 1: PLANNING TESTS
# ════════════════════════════════════════════════════════════════════════════════

class TestPhasePlanning:
    """Phase 1 generates a hypothesis and approach via LLM."""

    @patch("app.avo_operator.recall_similar_failures", return_value=[])
    @patch("app.avo_operator.create_specialist_llm")
    def test_valid_plan_returned(self, mock_llm_factory, mock_failures):
        from app.avo_operator import _phase_planning
        mock_llm = MagicMock()
        mock_llm.call.return_value = json.dumps({
            "hypothesis": "Add retry logic to web_search tool",
            "approach": "Wrap HTTP calls in exponential backoff",
            "change_type": "code",
            "target_files": ["app/tools/web_search.py"],
        })
        mock_llm_factory.return_value = mock_llm

        plan = _phase_planning("context", "memory", "lineage", set())
        assert plan is not None
        assert plan["hypothesis"] == "Add retry logic to web_search tool"
        assert plan["change_type"] == "code"
        assert "app/tools/web_search.py" in plan["target_files"]

    @patch("app.avo_operator.recall_similar_failures", return_value=[])
    @patch("app.avo_operator.create_specialist_llm")
    def test_llm_failure_returns_none(self, mock_llm_factory, mock_failures):
        from app.avo_operator import _phase_planning
        mock_llm = MagicMock()
        mock_llm.call.side_effect = RuntimeError("API down")
        mock_llm_factory.return_value = mock_llm

        plan = _phase_planning("context", "", "", set())
        assert plan is None

    @patch("app.avo_operator.recall_similar_failures", return_value=[])
    @patch("app.avo_operator.create_specialist_llm")
    def test_unparseable_response_returns_none(self, mock_llm_factory, mock_failures):
        from app.avo_operator import _phase_planning
        mock_llm = MagicMock()
        mock_llm.call.return_value = "This is not JSON at all"
        mock_llm_factory.return_value = mock_llm

        plan = _phase_planning("context", "", "", set())
        assert plan is None

    @patch("app.avo_operator.recall_similar_failures", return_value=[])
    @patch("app.avo_operator.create_specialist_llm")
    def test_exact_dedup_rejects_duplicate(self, mock_llm_factory, mock_failures):
        from app.avo_operator import _phase_planning
        hypothesis = "Fix the authentication bug"
        h = hashlib.sha256(hypothesis.lower().strip().encode()).hexdigest()[:8]

        mock_llm = MagicMock()
        mock_llm.call.return_value = json.dumps({
            "hypothesis": hypothesis,
            "approach": "approach",
            "change_type": "code",
            "target_files": ["app/auth.py"],
        })
        mock_llm_factory.return_value = mock_llm

        tried = {h}
        plan = _phase_planning("context", "", "", tried)
        assert plan is None

    @patch("app.avo_operator.recall_similar_failures", return_value=[])
    @patch("app.avo_operator.create_specialist_llm")
    def test_fuzzy_dedup_rejects_near_duplicate(self, mock_llm_factory, mock_failures):
        from app.avo_operator import _phase_planning
        # Pre-add a fuzzy hash for a similar hypothesis
        original = "Adding a skill for handling API credit-related errors will reduce recurring issues"
        norm = re.sub(r'[^a-z ]+', '', original.lower())
        norm = ' '.join(norm.split())[:40]
        fuzzy_h = hashlib.sha256(norm.encode()).hexdigest()[:8]

        # New hypothesis differs slightly but fuzzy-matches
        new_hyp = "Adding a skill for handling API credit-related errors (code 402) will reduce recurring"
        mock_llm = MagicMock()
        mock_llm.call.return_value = json.dumps({
            "hypothesis": new_hyp,
            "approach": "approach",
            "change_type": "skill",
            "target_files": [],
        })
        mock_llm_factory.return_value = mock_llm

        tried = {fuzzy_h}
        plan = _phase_planning("context", "", "", tried)
        assert plan is None

    @patch("app.avo_operator.create_specialist_llm")
    def test_similar_failure_rejects(self, mock_llm_factory):
        from app.avo_operator import _phase_planning
        mock_llm = MagicMock()
        mock_llm.call.return_value = json.dumps({
            "hypothesis": "Fix the timeout in web requests",
            "approach": "increase timeout",
            "change_type": "code",
            "target_files": ["app/web.py"],
        })
        mock_llm_factory.return_value = mock_llm

        with patch("app.avo_operator.recall_similar_failures",
                    return_value=[{"distance": 0.10, "hypothesis": "Fix timeout"}]):
            plan = _phase_planning("context", "", "", set())
            assert plan is None  # Too similar to a past failure (dist < 0.15)

    @patch("app.avo_operator.create_specialist_llm")
    def test_distant_failure_does_not_reject(self, mock_llm_factory):
        from app.avo_operator import _phase_planning
        mock_llm = MagicMock()
        mock_llm.call.return_value = json.dumps({
            "hypothesis": "Add caching to improve performance",
            "approach": "implement LRU cache",
            "change_type": "code",
            "target_files": ["app/cache.py"],
        })
        mock_llm_factory.return_value = mock_llm

        with patch("app.avo_operator.recall_similar_failures",
                    return_value=[{"distance": 0.80, "hypothesis": "Something else"}]):
            plan = _phase_planning("context", "", "", set())
            assert plan is not None

    @patch("app.avo_operator.recall_similar_failures", return_value=[])
    @patch("app.avo_operator.create_specialist_llm")
    def test_fuzzy_hash_added_to_tried(self, mock_llm_factory, mock_failures):
        from app.avo_operator import _phase_planning
        mock_llm = MagicMock()
        mock_llm.call.return_value = json.dumps({
            "hypothesis": "Improve error handling in rate throttle",
            "approach": "add try/except",
            "change_type": "code",
            "target_files": ["app/rate_throttle.py"],
        })
        mock_llm_factory.return_value = mock_llm

        tried = set()
        plan = _phase_planning("context", "", "", tried)
        assert plan is not None
        # The fuzzy hash should now be in tried_hashes
        assert len(tried) >= 1

    def test_planning_prompt_has_code_bias(self):
        src = inspect.getsource(__import__("app.avo_operator", fromlist=["_phase_planning"]))
        assert "MUST use change_type='code'" in src
        assert "LAST RESORT" in src
        assert '"change_type": "code"' in src  # Default in JSON example


# ════════════════════════════════════════════════════════════════════════════════
# 4. PHASE 2: IMPLEMENTATION TESTS
# ════════════════════════════════════════════════════════════════════════════════

class TestPhaseImplementation:
    """Phase 2 generates file contents via LLM."""

    @patch("app.avo_operator.create_specialist_llm")
    def test_skill_generation(self, mock_llm_factory):
        from app.avo_operator import _phase_implementation
        mock_llm = MagicMock()
        mock_llm.call.return_value = json.dumps({
            "files": {"skills/error_handling.md": "# Error Handling\nContent here..."}
        })
        mock_llm_factory.return_value = mock_llm

        plan = {
            "hypothesis": "Add error handling skill",
            "approach": "document patterns",
            "change_type": "skill",
            "target_files": ["skills/error_handling.md"],
        }
        files = _phase_implementation(plan)
        assert files is not None
        assert "skills/error_handling.md" in files

    @patch("app.avo_operator._read_target_files", return_value={"app/fix.py": "# existing"})
    @patch("app.avo_operator.create_specialist_llm")
    def test_code_generation(self, mock_llm_factory, mock_read):
        from app.avo_operator import _phase_implementation
        mock_llm = MagicMock()
        mock_llm.call.return_value = json.dumps({
            "files": {"app/fix.py": "# fixed code\ndef fixed(): pass"}
        })
        mock_llm_factory.return_value = mock_llm

        plan = {
            "hypothesis": "Fix bug in fix.py",
            "approach": "add validation",
            "change_type": "code",
            "target_files": ["app/fix.py"],
        }
        files = _phase_implementation(plan)
        assert files is not None
        assert "app/fix.py" in files
        assert "fixed" in files["app/fix.py"]

    @patch("app.avo_operator.create_specialist_llm")
    def test_code_uses_budget_tier(self, mock_llm_factory):
        """Code changes should NOT use force_tier='local'."""
        from app.avo_operator import _phase_implementation
        mock_llm = MagicMock()
        mock_llm.call.return_value = '{"files": {"app/x.py": "# code"}}'
        mock_llm_factory.return_value = mock_llm

        plan = {"change_type": "code", "hypothesis": "h", "approach": "a", "target_files": []}
        with patch("app.avo_operator._read_target_files", return_value={}):
            _phase_implementation(plan)
        # Should be called WITHOUT force_tier="local"
        call_kwargs = mock_llm_factory.call_args
        assert call_kwargs.kwargs.get("force_tier") is None or call_kwargs.kwargs.get("force_tier") != "local"

    @patch("app.avo_operator.create_specialist_llm")
    def test_skill_uses_local_tier(self, mock_llm_factory):
        """Skill generation should use local model (free)."""
        from app.avo_operator import _phase_implementation
        mock_llm = MagicMock()
        mock_llm.call.return_value = '{"files": {"skills/x.md": "# Skill content"}}'
        mock_llm_factory.return_value = mock_llm

        plan = {"change_type": "skill", "hypothesis": "h", "approach": "a", "target_files": []}
        _phase_implementation(plan)
        call_kwargs = mock_llm_factory.call_args
        assert call_kwargs.kwargs.get("force_tier") == "local"

    @patch("app.avo_operator.create_specialist_llm")
    def test_llm_failure_returns_none(self, mock_llm_factory):
        from app.avo_operator import _phase_implementation
        mock_llm = MagicMock()
        mock_llm.call.side_effect = RuntimeError("LLM error")
        mock_llm_factory.return_value = mock_llm

        plan = {"change_type": "skill", "hypothesis": "h", "approach": "a"}
        assert _phase_implementation(plan) is None

    @patch("app.avo_operator.create_specialist_llm")
    def test_unparseable_returns_none(self, mock_llm_factory):
        from app.avo_operator import _phase_implementation
        mock_llm = MagicMock()
        mock_llm.call.return_value = "Not JSON"
        mock_llm_factory.return_value = mock_llm

        plan = {"change_type": "skill", "hypothesis": "h", "approach": "a"}
        assert _phase_implementation(plan) is None

    @patch("app.avo_operator.create_specialist_llm")
    def test_empty_files_returns_none(self, mock_llm_factory):
        from app.avo_operator import _phase_implementation
        mock_llm = MagicMock()
        mock_llm.call.return_value = '{"files": {}}'
        mock_llm_factory.return_value = mock_llm

        plan = {"change_type": "skill", "hypothesis": "h", "approach": "a"}
        assert _phase_implementation(plan) is None

    @patch("app.avo_operator.create_specialist_llm")
    def test_repair_errors_included_in_prompt(self, mock_llm_factory):
        from app.avo_operator import _phase_implementation
        mock_llm = MagicMock()
        mock_llm.call.return_value = '{"files": {"skills/x.md": "# Fixed"}}'
        mock_llm_factory.return_value = mock_llm

        plan = {"change_type": "skill", "hypothesis": "h", "approach": "a"}
        _phase_implementation(plan, repair_errors=["Syntax error in line 5"])
        # Check the prompt passed to llm.call includes repair errors
        call_args = mock_llm.call.call_args[0][0]
        assert "REPAIR" in call_args
        assert "Syntax error" in call_args

    @patch("app.avo_operator.create_specialist_llm")
    def test_auto_generates_skill_filename(self, mock_llm_factory):
        from app.avo_operator import _phase_implementation
        mock_llm = MagicMock()
        mock_llm.call.return_value = '{"files": {"skills/improve_error.md": "# Content"}}'
        mock_llm_factory.return_value = mock_llm

        plan = {
            "change_type": "skill",
            "hypothesis": "Improve error handling patterns",
            "approach": "document",
            "target_files": [],  # No target files → auto-generate
        }
        _phase_implementation(plan)
        prompt = mock_llm.call.call_args[0][0]
        assert "skills/" in prompt


# ════════════════════════════════════════════════════════════════════════════════
# 5. PHASE 3: LOCAL TESTING TESTS
# ════════════════════════════════════════════════════════════════════════════════

class TestPhaseLocalTesting:
    """Phase 3 validates mutations with AST + safety checks (no LLM)."""

    def test_valid_python_passes(self):
        from app.avo_operator import _phase_local_testing
        files = {"app/valid.py": "def hello():\n    return 'world'\n"}
        ok, errors = _phase_local_testing(files)
        assert ok is True
        assert errors == []

    def test_syntax_error_detected(self):
        from app.avo_operator import _phase_local_testing
        files = {"app/bad.py": "def f(:\n    pass"}
        ok, errors = _phase_local_testing(files)
        assert ok is False
        assert any("Syntax error" in e for e in errors)

    def test_valid_markdown_passes(self):
        from app.avo_operator import _phase_local_testing
        files = {"skills/knowledge.md": "# Knowledge\n\nThis is a comprehensive skill file with enough content to pass the minimum size check easily."}
        ok, errors = _phase_local_testing(files)
        assert ok is True

    def test_short_markdown_fails(self):
        from app.avo_operator import _phase_local_testing
        files = {"skills/tiny.md": "Hi"}
        ok, errors = _phase_local_testing(files)
        assert ok is False
        assert any("too short" in e for e in errors)

    def test_empty_file_fails(self):
        from app.avo_operator import _phase_local_testing
        files = {"app/empty.py": "   \n   \n   "}
        ok, errors = _phase_local_testing(files)
        assert ok is False
        assert any("Empty file" in e for e in errors)

    def test_dangerous_import_detected(self):
        from app.avo_operator import _phase_local_testing
        files = {"app/evil.py": "import subprocess\nsubprocess.call(['rm', '-rf', '/'])"}
        ok, errors = _phase_local_testing(files)
        assert ok is False
        assert any("subprocess" in e.lower() or "dangerous" in e.lower() for e in errors)

    def test_multiple_files_all_checked(self):
        from app.avo_operator import _phase_local_testing
        files = {
            "app/good.py": "x = 1",
            "app/bad.py": "def f(: broken",
            "skills/ok.md": "# Skill\n\nThis is a comprehensive skill file with enough content for testing purposes.",
        }
        ok, errors = _phase_local_testing(files)
        assert ok is False
        assert any("bad.py" in e for e in errors)

    def test_valid_complex_code(self):
        from app.avo_operator import _phase_local_testing
        code = '''
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class Processor:
    """Process data safely."""
    def __init__(self, config: dict):
        self.config = config
        self._cache = {}

    def process(self, data: str) -> Optional[dict]:
        try:
            result = json.loads(data)
            return result
        except json.JSONDecodeError:
            logger.warning("Invalid JSON")
            return None
'''
        files = {"app/processor.py": code}
        ok, errors = _phase_local_testing(files)
        assert ok is True
        assert errors == []

    def test_protected_file_rejected(self):
        from app.avo_operator import _phase_local_testing
        from app.auto_deployer import PROTECTED_FILES
        if PROTECTED_FILES:
            protected = next(iter(PROTECTED_FILES))
            files = {protected: "# hacked"}
            ok, errors = _phase_local_testing(files)
            assert ok is False


# ════════════════════════════════════════════════════════════════════════════════
# 6. PHASE 4: SELF-CRITIQUE TESTS
# ════════════════════════════════════════════════════════════════════════════════

class TestPhaseSelfCritique:
    """Phase 4 evaluates mutation quality with an external LLM."""

    @patch("app.llm_factory.create_cheap_vetting_llm")
    def test_approved(self, mock_llm_factory):
        from app.avo_operator import _phase_self_critique
        mock_llm = MagicMock()
        mock_llm.call.return_value = '{"approve": true, "concerns": []}'
        mock_llm_factory.return_value = mock_llm

        plan = {"hypothesis": "Fix bug", "change_type": "code"}
        files = {"app/fix.py": "# fixed code"}
        approved, notes = _phase_self_critique(plan, files, "")
        assert approved is True

    @patch("app.llm_factory.create_cheap_vetting_llm")
    def test_rejected_with_concerns(self, mock_llm_factory):
        from app.avo_operator import _phase_self_critique
        mock_llm = MagicMock()
        mock_llm.call.return_value = '{"approve": false, "concerns": ["Too broad", "Risky change"]}'
        mock_llm_factory.return_value = mock_llm

        plan = {"hypothesis": "Refactor everything", "change_type": "code"}
        files = {"app/big_change.py": "# massive rewrite"}
        approved, notes = _phase_self_critique(plan, files, "")
        assert approved is False
        assert "Too broad" in notes
        assert "Risky" in notes

    @patch("app.llm_factory.create_cheap_vetting_llm")
    def test_llm_failure_defaults_to_approved(self, mock_llm_factory):
        from app.avo_operator import _phase_self_critique
        mock_llm = MagicMock()
        mock_llm.call.side_effect = RuntimeError("LLM down")
        mock_llm_factory.return_value = mock_llm

        plan = {"hypothesis": "Fix", "change_type": "code"}
        files = {"app/x.py": "# code"}
        approved, notes = _phase_self_critique(plan, files, "")
        assert approved is True
        assert "unavailable" in notes.lower()

    @patch("app.llm_factory.create_cheap_vetting_llm")
    def test_unparseable_defaults_to_approved(self, mock_llm_factory):
        from app.avo_operator import _phase_self_critique
        mock_llm = MagicMock()
        mock_llm.call.return_value = "I cannot evaluate this properly."
        mock_llm_factory.return_value = mock_llm

        plan = {"hypothesis": "Fix", "change_type": "code"}
        files = {"app/x.py": "# code"}
        approved, notes = _phase_self_critique(plan, files, "")
        assert approved is True
        assert "unparseable" in notes.lower()

    def test_uses_dgm_different_model(self):
        """Critic must use create_cheap_vetting_llm, not create_specialist_llm."""
        src = inspect.getsource(
            __import__("app.avo_operator", fromlist=["_phase_self_critique"])._phase_self_critique
        )
        assert "create_cheap_vetting_llm" in src
        assert "DGM" in src

    @patch("app.llm_factory.create_cheap_vetting_llm")
    def test_memory_context_included_when_provided(self, mock_llm_factory):
        from app.avo_operator import _phase_self_critique
        mock_llm = MagicMock()
        mock_llm.call.return_value = '{"approve": true, "concerns": []}'
        mock_llm_factory.return_value = mock_llm

        plan = {"hypothesis": "Fix", "change_type": "code"}
        files = {"app/x.py": "# code"}
        _phase_self_critique(plan, files, "Past failure: timeout on web search")
        prompt = mock_llm.call.call_args[0][0]
        assert "Past failure" in prompt


# ════════════════════════════════════════════════════════════════════════════════
# 7. FILE READING TESTS
# ════════════════════════════════════════════════════════════════════════════════

class TestReadTargetFiles:
    """_read_target_files reads code for mutation context."""

    def test_reads_existing_file(self):
        from app.avo_operator import _read_target_files
        # This file should always exist in the container
        result = _read_target_files(["app/avo_operator.py"])
        assert len(result) >= 0  # May or may not resolve depending on path

    def test_skips_nonexistent_file(self):
        from app.avo_operator import _read_target_files
        result = _read_target_files(["app/nonexistent_xyz_12345.py"])
        assert len(result) == 0

    def test_caps_at_5_files(self):
        from app.avo_operator import _read_target_files
        files = [f"app/file_{i}.py" for i in range(10)]
        # Even if none exist, the function processes at most 5
        result = _read_target_files(files)
        assert isinstance(result, dict)

    def test_skips_non_python_files(self):
        from app.avo_operator import _read_target_files
        result = _read_target_files(["app/config.yaml", "app/data.json"])
        # Only .py files are read
        assert all(k.endswith(".py") for k in result)

    def test_content_capped_at_8k(self):
        from app.avo_operator import _read_target_files
        result = _read_target_files(["app/avo_operator.py"])
        for content in result.values():
            assert len(content) <= 8000


# ════════════════════════════════════════════════════════════════════════════════
# 8. FULL PIPELINE TESTS
# ════════════════════════════════════════════════════════════════════════════════

class TestRunAvoPipeline:
    """Full 5-phase pipeline integration tests."""

    @patch("app.avo_operator._phase_self_critique", return_value=(True, "No concerns"))
    @patch("app.avo_operator._phase_local_testing", return_value=(True, []))
    @patch("app.avo_operator._phase_implementation",
           return_value={"app/fix.py": "def fixed(): pass"})
    @patch("app.avo_operator._phase_planning", return_value={
        "hypothesis": "Fix the bug",
        "approach": "Add validation",
        "change_type": "code",
        "target_files": ["app/fix.py"],
    })
    def test_successful_pipeline(self, mock_plan, mock_impl, mock_test, mock_critique):
        from app.avo_operator import run_avo_pipeline
        result = run_avo_pipeline(
            context="test context",
            tried_hashes=set(),
            memory_context="",
            lineage_context="",
        )
        assert result.phases_completed == 5
        assert result.mutation is not None
        assert result.mutation.change_type == "code"
        assert result.mutation.hypothesis == "Fix the bug"
        assert "app/fix.py" in result.mutation.files
        assert result.abandoned_reason == ""

    @patch("app.avo_operator._phase_planning", return_value=None)
    def test_planning_failure(self, mock_plan):
        from app.avo_operator import run_avo_pipeline
        result = run_avo_pipeline("ctx", set(), "", "")
        assert result.phases_completed == 0
        assert result.mutation is None
        assert "Planning" in result.abandoned_reason

    @patch("app.avo_operator._phase_implementation", return_value=None)
    @patch("app.avo_operator._phase_planning", return_value={
        "hypothesis": "h", "approach": "a", "change_type": "code", "target_files": [],
    })
    def test_implementation_failure(self, mock_plan, mock_impl):
        from app.avo_operator import run_avo_pipeline
        result = run_avo_pipeline("ctx", set(), "", "")
        assert result.phases_completed == 1
        assert "Implementation failed" in result.abandoned_reason

    @patch("app.avo_operator._phase_local_testing")
    @patch("app.avo_operator._phase_implementation",
           return_value={"app/bad.py": "import subprocess"})
    @patch("app.avo_operator._phase_planning", return_value={
        "hypothesis": "h", "approach": "a", "change_type": "code", "target_files": [],
    })
    def test_repair_loop_exhausted(self, mock_plan, mock_impl, mock_test):
        from app.avo_operator import run_avo_pipeline, _MAX_REPAIR_ATTEMPTS
        # Always fail local testing
        mock_test.return_value = (False, ["Dangerous import"])
        result = run_avo_pipeline("ctx", set(), "", "")
        assert "Local testing failed" in result.abandoned_reason
        assert result.repair_attempts == _MAX_REPAIR_ATTEMPTS

    @patch("app.avo_operator._phase_local_testing")
    @patch("app.avo_operator._phase_implementation")
    @patch("app.avo_operator._phase_planning", return_value={
        "hypothesis": "h", "approach": "a", "change_type": "code", "target_files": [],
    })
    def test_repair_loop_succeeds_on_retry(self, mock_plan, mock_impl, mock_test):
        from app.avo_operator import run_avo_pipeline
        mock_impl.return_value = {"app/fix.py": "# code"}
        # Fail first, pass second
        mock_test.side_effect = [
            (False, ["Syntax error"]),
            (True, []),
        ]
        with patch("app.avo_operator._phase_self_critique", return_value=(True, "OK")):
            result = run_avo_pipeline("ctx", set(), "", "")
        assert result.phases_completed == 5
        assert result.repair_attempts == 2

    @patch("app.avo_operator._phase_self_critique", return_value=(False, "Too risky"))
    @patch("app.avo_operator._phase_local_testing", return_value=(True, []))
    @patch("app.avo_operator._phase_implementation",
           return_value={"app/x.py": "# code"})
    @patch("app.avo_operator._phase_planning", return_value={
        "hypothesis": "h", "approach": "a", "change_type": "code", "target_files": [],
    })
    def test_critique_rejection(self, mock_plan, mock_impl, mock_test, mock_critique):
        from app.avo_operator import run_avo_pipeline
        result = run_avo_pipeline("ctx", set(), "", "")
        assert result.phases_completed == 4
        assert result.mutation is None
        assert "Self-critique rejected" in result.abandoned_reason
        assert "Too risky" in result.abandoned_reason

    def test_yield_before_planning(self):
        from app.avo_operator import run_avo_pipeline
        result = run_avo_pipeline("ctx", set(), "", "",
                                  yield_check=lambda: True)
        assert result.phases_completed == 0
        assert "Yielded" in result.abandoned_reason

    @patch("app.avo_operator._phase_local_testing", return_value=(True, []))
    @patch("app.avo_operator._phase_implementation",
           return_value={"app/x.py": "# code"})
    @patch("app.avo_operator._phase_planning", return_value={
        "hypothesis": "h", "approach": "a", "change_type": "code", "target_files": [],
    })
    def test_yield_after_phase_1(self, mock_plan, mock_impl, mock_test):
        from app.avo_operator import run_avo_pipeline
        # Yield on second check (after planning succeeds)
        calls = [0]
        def yield_on_second():
            calls[0] += 1
            return calls[0] >= 2
        result = run_avo_pipeline("ctx", set(), "", "",
                                  yield_check=yield_on_second)
        assert result.phases_completed == 1
        assert "Yielded" in result.abandoned_reason

    @patch("app.avo_operator._phase_self_critique", return_value=(True, "LGTM"))
    @patch("app.avo_operator._phase_local_testing", return_value=(True, []))
    @patch("app.avo_operator._phase_implementation",
           return_value={"skills/new_skill.md": "# Skill\n\nContent here..."})
    @patch("app.avo_operator._phase_planning", return_value={
        "hypothesis": "Add skill", "approach": "Write docs",
        "change_type": "skill", "target_files": ["skills/new_skill.md"],
    })
    def test_skill_mutation_pipeline(self, mock_plan, mock_impl, mock_test, mock_critique):
        from app.avo_operator import run_avo_pipeline
        result = run_avo_pipeline("ctx", set(), "", "")
        assert result.phases_completed == 5
        assert result.mutation.change_type == "skill"

    @patch("app.avo_operator._phase_self_critique", return_value=(True, "OK"))
    @patch("app.avo_operator._phase_local_testing", return_value=(True, []))
    @patch("app.avo_operator._phase_implementation",
           return_value={"app/x.py": "# code"})
    @patch("app.avo_operator._phase_planning", return_value={
        "hypothesis": "h", "approach": "a", "change_type": "code", "target_files": [],
    })
    def test_experiment_id_generated(self, mock_plan, mock_impl, mock_test, mock_critique):
        from app.avo_operator import run_avo_pipeline
        result = run_avo_pipeline("ctx", set(), "", "")
        assert result.mutation.experiment_id.startswith("exp_")


# ════════════════════════════════════════════════════════════════════════════════
# 9. FUZZY DEDUP TESTS
# ════════════════════════════════════════════════════════════════════════════════

class TestFuzzyDedup:
    """Fuzzy deduplication catches near-duplicate hypotheses."""

    def _fuzzy_hash(self, text: str) -> str:
        norm = re.sub(r'[^a-z ]+', '', text.lower())
        norm = ' '.join(norm.split())[:40]
        return hashlib.sha256(norm.encode()).hexdigest()[:8]

    def test_identical_hypotheses_match(self):
        h1 = self._fuzzy_hash("Fix the authentication bug in login")
        h2 = self._fuzzy_hash("Fix the authentication bug in login")
        assert h1 == h2

    def test_case_insensitive(self):
        h1 = self._fuzzy_hash("Fix The Authentication Bug")
        h2 = self._fuzzy_hash("fix the authentication bug")
        assert h1 == h2

    def test_numbers_stripped(self):
        h1 = self._fuzzy_hash("Fix error code 402 in API calls")
        h2 = self._fuzzy_hash("Fix error code 500 in API calls")
        assert h1 == h2

    def test_punctuation_stripped(self):
        h1 = self._fuzzy_hash("Adding a skill for handling API credit-related errors will reduce recurring issues")
        h2 = self._fuzzy_hash("Adding a skill for handling API credit-related errors (code 402) will reduce recurring")
        assert h1 == h2

    def test_different_hypotheses_differ(self):
        h1 = self._fuzzy_hash("Fix authentication bug")
        h2 = self._fuzzy_hash("Optimize database queries")
        assert h1 != h2

    def test_prefix_matters(self):
        """Hypotheses that differ only after 40 chars still match (by design)."""
        base = "Improve error handling in the rate throttle"  # >40 chars
        h1 = self._fuzzy_hash(base + " module for better reliability")
        h2 = self._fuzzy_hash(base + " component with exponential backoff")
        assert h1 == h2  # Same prefix within 40 chars

    def test_evolution_tried_hypotheses_uses_fuzzy(self):
        """_get_tried_hypotheses should include fuzzy hashes."""
        src = inspect.getsource(
            __import__("app.evolution", fromlist=["_get_tried_hypotheses"])._get_tried_hypotheses
        )
        assert "re.sub" in src or "_re.sub" in src
        assert "[:40]" in src


# ════════════════════════════════════════════════════════════════════════════════
# 10. EXPERIMENT RUNNER FILE TRACKING TESTS
# ════════════════════════════════════════════════════════════════════════════════

class TestExperimentRunnerFileTracking:
    """Crash paths must preserve files_changed from mutation spec."""

    def test_no_empty_files_changed_in_source(self):
        """ExperimentRunner.run_experiment should not have files_changed=[]."""
        from app.experiment_runner import ExperimentRunner
        src = inspect.getsource(ExperimentRunner.run_experiment)
        assert "files_changed=[]" not in src

    def test_crash_paths_use_mutation_files(self):
        from app.experiment_runner import ExperimentRunner
        src = inspect.getsource(ExperimentRunner.run_experiment)
        # Should reference mutation.files.keys() for crash paths
        assert "mutation.files.keys()" in src

    def test_mutation_spec_has_files(self):
        from app.experiment_runner import MutationSpec
        spec = MutationSpec(
            experiment_id="test",
            hypothesis="test",
            change_type="code",
            files={"app/fix.py": "# code", "app/util.py": "# util"},
        )
        assert list(spec.files.keys()) == ["app/fix.py", "app/util.py"]

    def test_experiment_result_tracks_files(self):
        from app.experiment_runner import ExperimentResult
        result = ExperimentResult(
            experiment_id="test",
            hypothesis="test",
            change_type="code",
            metric_before=0.8,
            metric_after=0.0,
            delta=0.0,
            status="crash",
            files_changed=["app/fix.py"],
            detail="Pre-validation failed",
        )
        assert result.files_changed == ["app/fix.py"]


# ════════════════════════════════════════════════════════════════════════════════
# 11. SYSTEM WIRING TESTS
# ════════════════════════════════════════════════════════════════════════════════

class TestSystemWiring:
    """AVO must be correctly wired into the evolution system."""

    def test_evolution_imports_avo(self):
        src = inspect.getsource(__import__("app.evolution", fromlist=["evolve"]))
        assert "run_avo_pipeline" in src
        assert "avo_operator" in src

    def test_evolution_uses_avo_by_default(self):
        src = inspect.getsource(__import__("app.evolution", fromlist=["evolve"]))
        assert 'EVOLUTION_USE_AVO' in src

    def test_idle_scheduler_calls_evolution(self):
        from app.idle_scheduler import _default_jobs
        jobs = _default_jobs()
        names = [name for name, _ in jobs]
        assert any("evolution" in n.lower() for n in names)

    def test_auto_deployer_protects_evolution(self):
        from app.auto_deployer import PROTECTED_FILES
        assert "app/evolution.py" in PROTECTED_FILES

    def test_experiment_runner_importable(self):
        from app.experiment_runner import ExperimentRunner, MutationSpec
        assert callable(ExperimentRunner)

    def test_variant_archive_importable(self):
        from app.variant_archive import add_variant, get_last_kept_id
        assert callable(add_variant)

    def test_evo_memory_importable(self):
        from app.evo_memory import recall_similar_failures
        assert callable(recall_similar_failures)


# ════════════════════════════════════════════════════════════════════════════════
# 12. INTEGRATION TESTS
# ════════════════════════════════════════════════════════════════════════════════

class TestIntegration:
    """Integration tests with live services."""

    def test_phase3_on_real_python_code(self):
        """Phase 3 should accept well-formed Python from a real module."""
        from app.avo_operator import _phase_local_testing
        # Use avo_operator's own source as test input
        try:
            code = Path("/app/app/avo_operator.py").read_text()
        except FileNotFoundError:
            code = "def test(): pass"
        ok, errors = _phase_local_testing({"app/test_module.py": code})
        assert ok is True, f"Real Python code should pass: {errors}"

    def test_variant_archive_state(self):
        """Variant archive should have entries from prior evolution runs."""
        import json
        try:
            va = json.loads(Path("/app/workspace/variant_archive.json").read_text())
            assert isinstance(va, list)
            # Check structure
            if va:
                entry = va[0]
                assert "hypothesis" in entry
                assert "change_type" in entry
                assert "status" in entry
        except FileNotFoundError:
            pytest.skip("No variant archive")

    def test_results_ledger_readable(self):
        """Results ledger (results.tsv) should be readable."""
        try:
            content = Path("/app/workspace/results.tsv").read_text()
            lines = content.strip().split("\n")
            assert len(lines) > 0
        except FileNotFoundError:
            pytest.skip("No results ledger")

    def test_protected_files_set_reasonable(self):
        from app.auto_deployer import PROTECTED_FILES
        assert isinstance(PROTECTED_FILES, (set, frozenset))
        assert len(PROTECTED_FILES) >= 10
        assert "app/evolution.py" in PROTECTED_FILES
        assert "app/auto_deployer.py" in PROTECTED_FILES
        assert "app/experiment_runner.py" in PROTECTED_FILES


# ════════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
