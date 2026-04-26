"""Tests for the 5 elegance-enforcing fixes (A through E).

Covers:
  A. Coding conventions injected into AVO planning + critique prompts
  B. code_quality module: per-file scoring + mutation gate
  C. Strengthened critique rubric: hard rejects, smell detection, score floor
  D. Pattern library quality filter: rejects patterns from quality-regressing experiments
  E. Architectural review: cycle detection, capability overlap, centrality spike

Each test exercises a public API end-to-end without requiring Docker, ChromaDB,
or LLM availability — the modules degrade gracefully when those are missing.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.test_metrics import _FakeSettings  # noqa: E402
import app.config as config_mod  # noqa: E402

config_mod.get_settings = lambda: _FakeSettings()
config_mod.get_anthropic_api_key = lambda: "fake-key"
config_mod.get_gateway_secret = lambda: "a" * 64


# ─────────────────────────────────────────────────────────────────────────────
# Fix A: Coding conventions injection
# ─────────────────────────────────────────────────────────────────────────────

class TestCodingConventionsInjection:
    def test_conventions_file_exists(self):
        """The conventions file should ship with the repo so production loads it."""
        from pathlib import Path
        path = Path(__file__).parent.parent / "workspace" / "meta" / "coding_conventions.md"
        assert path.exists()
        content = path.read_text()
        # Spot-check that the rules from CLAUDE.md are present
        assert "type hints" in content.lower()
        assert "pathlib" in content.lower()
        assert "logger" in content.lower()

    def test_load_meta_prompt_returns_content(self, tmp_path, monkeypatch):
        """The AVO _load_meta_prompt helper should read coding_conventions.md."""
        import app.avo_operator as avo
        monkeypatch.setattr(avo, "_META_DIR", tmp_path)
        (tmp_path / "coding_conventions.md").write_text("# Test conventions\nUse pathlib.")
        result = avo._load_meta_prompt("coding_conventions.md", "fallback")
        assert "pathlib" in result

    def test_load_meta_prompt_returns_fallback_when_missing(self, tmp_path, monkeypatch):
        import app.avo_operator as avo
        monkeypatch.setattr(avo, "_META_DIR", tmp_path)
        result = avo._load_meta_prompt("coding_conventions.md", "fallback content")
        assert result == "fallback content"


# ─────────────────────────────────────────────────────────────────────────────
# Fix B: Code quality module
# ─────────────────────────────────────────────────────────────────────────────

class TestCodeQualityScoring:
    def test_fully_typed_module_scores_high_on_type_coverage(self):
        from app.code_quality import measure_file_quality
        source = '''
from __future__ import annotations


def public_fn(x: int, y: str) -> bool:
    """Doc."""
    return True


def another(a: list[str]) -> None:
    """Doc."""
    pass
'''
        score = measure_file_quality(source)
        assert score.type_coverage == 1.0
        assert score.docstring_coverage == 1.0

    def test_untyped_module_scores_low_on_type_coverage(self):
        from app.code_quality import measure_file_quality
        source = '''
def public_fn(x, y):
    return True


def another(a):
    pass
'''
        score = measure_file_quality(source)
        assert score.type_coverage < 0.5
        assert score.docstring_coverage < 0.5

    def test_skips_dunder_and_private_functions(self):
        """`_helper` and dunders should not count against public coverage."""
        from app.code_quality import measure_file_quality
        source = '''
def _helper(x):
    return x


def public_one(x: int) -> int:
    """Doc."""
    return x
'''
        score = measure_file_quality(source)
        # Only public_one is scored — fully typed and documented
        assert score.type_coverage == 1.0

    def test_methods_skip_self_cls(self):
        """`self` and `cls` should not require type annotations."""
        from app.code_quality import measure_file_quality
        source = '''
class Foo:
    def method(self, x: int) -> bool:
        """Doc."""
        return True

    @classmethod
    def class_method(cls, y: str) -> None:
        """Doc."""
        pass
'''
        score = measure_file_quality(source)
        # Both methods have x/y typed and return type — fully typed
        assert score.type_coverage == 1.0


class TestQualityRegressionGate:
    def test_drop_in_type_coverage_is_regression(self):
        from app.code_quality import evaluate_mutation_quality

        before = '''
def fn(x: int, y: str) -> bool:
    """Doc."""
    return True
'''
        after = '''
def fn(x, y):
    return True
'''
        report = evaluate_mutation_quality(
            files_before={"app/foo.py": before},
            files_after={"app/foo.py": after},
        )
        assert report.has_regression
        assert report.worst_regression < 0

    def test_no_change_is_not_regression(self):
        from app.code_quality import evaluate_mutation_quality

        source = '''
def fn(x: int) -> bool:
    """Doc."""
    return True
'''
        report = evaluate_mutation_quality(
            files_before={"app/foo.py": source},
            files_after={"app/foo.py": source},
        )
        assert not report.has_regression

    def test_improvement_is_not_regression(self):
        from app.code_quality import evaluate_mutation_quality

        before = '''
def fn(x, y):
    return True
'''
        after = '''
def fn(x: int, y: str) -> bool:
    """Doc."""
    return True
'''
        report = evaluate_mutation_quality(
            files_before={"app/foo.py": before},
            files_after={"app/foo.py": after},
        )
        assert not report.has_regression

    def test_non_python_files_skipped(self):
        from app.code_quality import evaluate_mutation_quality

        report = evaluate_mutation_quality(
            files_before={"workspace/foo.md": "# old"},
            files_after={"workspace/foo.md": "# new"},
        )
        # Markdown is skipped — no Python files → no regression possible
        assert not report.has_regression


# ─────────────────────────────────────────────────────────────────────────────
# Fix C: Critique rubric
# ─────────────────────────────────────────────────────────────────────────────

class TestCritiqueRubric:
    def test_critique_prompt_file_exists(self):
        from pathlib import Path
        path = Path(__file__).parent.parent / "workspace" / "meta" / "avo_critique_prompt.md"
        assert path.exists()
        content = path.read_text()
        assert "Hard Reject" in content or "hard reject" in content.lower()
        assert "rubric" in content.lower()

    def test_hard_rejects_force_disapproval(self):
        """When the LLM reports hard_rejects_triggered, approve must flip to false."""
        from app.avo_operator import _phase_self_critique

        # Mock the LLM to return a positive top-level approve but with hard rejects
        with patch("app.llm_factory.create_cheap_vetting_llm") as mock_factory:
            mock_llm = type("MockLLM", (), {})()
            mock_llm.call = lambda prompt: (
                '{"approve": true, "concerns": [], '
                '"rubric_score": 9, '
                '"smells_detected": [], '
                '"hard_rejects_triggered": ["bare except clause"]}'
            )
            mock_factory.return_value = mock_llm

            approved, notes = _phase_self_critique(
                plan={"hypothesis": "test", "change_type": "code"},
                files={"app/foo.py": "code"},
                memory_context="",
            )
            assert not approved
            assert "Hard reject" in notes

    def test_low_rubric_score_forces_disapproval(self):
        from app.avo_operator import _phase_self_critique

        with patch("app.llm_factory.create_cheap_vetting_llm") as mock_factory:
            mock_llm = type("MockLLM", (), {})()
            mock_llm.call = lambda prompt: (
                '{"approve": true, "concerns": [], '
                '"rubric_score": 5, '
                '"smells_detected": [], '
                '"hard_rejects_triggered": []}'
            )
            mock_factory.return_value = mock_llm

            approved, notes = _phase_self_critique(
                plan={"hypothesis": "test", "change_type": "code"},
                files={"app/foo.py": "code"},
                memory_context="",
            )
            assert not approved
            assert "Rubric score 5" in notes

    def test_two_smells_force_disapproval(self):
        from app.avo_operator import _phase_self_critique

        with patch("app.llm_factory.create_cheap_vetting_llm") as mock_factory:
            mock_llm = type("MockLLM", (), {})()
            mock_llm.call = lambda prompt: (
                '{"approve": true, "concerns": [], '
                '"rubric_score": 8, '
                '"smells_detected": ["wrapping over refactoring", "parameter explosion"], '
                '"hard_rejects_triggered": []}'
            )
            mock_factory.return_value = mock_llm

            approved, _ = _phase_self_critique(
                plan={"hypothesis": "test", "change_type": "code"},
                files={"app/foo.py": "code"},
                memory_context="",
            )
            assert not approved

    def test_clean_mutation_approves(self):
        from app.avo_operator import _phase_self_critique

        with patch("app.llm_factory.create_cheap_vetting_llm") as mock_factory:
            mock_llm = type("MockLLM", (), {})()
            mock_llm.call = lambda prompt: (
                '{"approve": true, "concerns": [], '
                '"rubric_score": 9, '
                '"smells_detected": [], '
                '"hard_rejects_triggered": []}'
            )
            mock_factory.return_value = mock_llm

            approved, _ = _phase_self_critique(
                plan={"hypothesis": "test", "change_type": "code"},
                files={"app/foo.py": "code"},
                memory_context="",
            )
            assert approved


# ─────────────────────────────────────────────────────────────────────────────
# Fix D: Pattern library quality filter
# ─────────────────────────────────────────────────────────────────────────────

class TestPatternLibraryQualityFilter:
    def test_quality_regressed_experiment_yields_no_pattern(self):
        from app.pattern_library import extract_pattern_from_experiment
        experiment = {
            "experiment_id": "exp_quality_fail",
            "hypothesis": "add caching",
            "delta": 0.10,
            "status": "keep",
            "detail": "Functional improvement (delta=+0.1) blocked by quality regression: type coverage dropped",
            "files_changed": ["app/cache.py"],
        }
        result = extract_pattern_from_experiment(experiment)
        assert result is None

    def test_clean_experiment_yields_pattern(self):
        from app.pattern_library import extract_pattern_from_experiment
        experiment = {
            "experiment_id": "exp_clean",
            "hypothesis": "add caching to LLM calls",
            "delta": 0.10,
            "status": "keep",
            "detail": "Improvement: +0.10",
            "files_changed": ["app/cache.py"],
        }
        result = extract_pattern_from_experiment(experiment)
        assert result is not None
        assert result.avg_delta == 0.10


# ─────────────────────────────────────────────────────────────────────────────
# Fix E: Architectural review
# ─────────────────────────────────────────────────────────────────────────────

class TestArchitecturalReview:
    def test_no_files_returns_empty_report(self):
        from app.architectural_review import review_mutation
        report = review_mutation({})
        assert not report.has_hard_rejects
        assert not report.has_soft_warnings

    def test_extract_local_imports(self):
        from app.architectural_review import _extract_local_imports
        source = (
            "from app.foo import bar\n"
            "from app.baz.qux import x\n"
            "import os  # not local\n"
            "import app.tools.web_search\n"
        )
        imports = _extract_local_imports(source)
        assert "app/foo.py" in imports
        assert "app/baz/qux.py" in imports
        assert "app/tools/web_search.py" in imports
        # Standard library imports not included
        assert not any("os" in i for i in imports)

    def test_cycle_detection_finds_cycles(self):
        """Construct a 2-cycle in the projected graph and verify detection."""
        from app.architectural_review import _detect_cycles

        files_after = {
            "app/a.py": "from app.b import x",
            "app/b.py": "from app.a import y",
        }
        existing_graph: dict[str, list[str]] = {}
        cycles = _detect_cycles(files_after, existing_graph)
        assert len(cycles) >= 1

    def test_no_cycle_in_acyclic_graph(self):
        from app.architectural_review import _detect_cycles

        files_after = {
            "app/a.py": "from app.b import x",
            "app/b.py": "from app.c import y",
        }
        existing_graph = {"app/c.py": []}
        cycles = _detect_cycles(files_after, existing_graph)
        assert len(cycles) == 0

    def test_capability_overlap_detection(self):
        from app.architectural_review import _detect_overlaps

        # File claims "evolution" capability, which 3 others already provide
        files_after = {
            "app/new_evolver.py": '"""evolution mutate variant fitness MAP-Elites"""',
        }
        capability_map = {
            "evolution": ["app/evolution.py", "app/avo_operator.py", "app/island_evolution.py"],
        }
        overlaps = _detect_overlaps(files_after, capability_map)
        assert len(overlaps) == 1
        assert overlaps[0].capability == "evolution"

    def test_review_summary_includes_findings(self):
        from app.architectural_review import (
            ReviewReport, CycleFinding, OverlapFinding, CentralityFinding,
        )
        report = ReviewReport(
            cycles=(CycleFinding(cycle=("app/a.py", "app/b.py", "app/a.py")),),
            overlaps=(OverlapFinding(
                filepath="app/x.py",
                capability="evolution",
                existing_owners=("app/y.py", "app/z.py"),
            ),),
        )
        summary = report.summary()
        assert "cycle" in summary.lower()
        assert "overlap" in summary.lower() or "duplicates" in summary.lower()


# ─────────────────────────────────────────────────────────────────────────────
# Integration: experiment_runner consults the quality gate
# ─────────────────────────────────────────────────────────────────────────────

class TestQualityGateIntegration:
    def test_evaluate_quality_returns_report_for_python_files(self):
        """The runner's _evaluate_quality method should produce a report."""
        from app.experiment_runner import ExperimentRunner, MutationSpec

        er = ExperimentRunner()
        mutation = MutationSpec(
            experiment_id="exp_q",
            hypothesis="test",
            change_type="code",
            files={"app/foo.py": "def f(x: int) -> int:\n    \"\"\"Doc.\"\"\"\n    return x"},
        )
        # Simulate that the file existed with worse quality before
        backed_up = {"app/foo.py": "def f(x):\n    return x"}
        report = er._evaluate_quality(mutation, backed_up)
        assert report is not None
        # New version is strictly better, so no regression
        assert not report.has_regression

    def test_evaluate_quality_returns_none_for_no_python_files(self):
        from app.experiment_runner import ExperimentRunner, MutationSpec

        er = ExperimentRunner()
        mutation = MutationSpec(
            experiment_id="exp_q",
            hypothesis="test",
            change_type="skill",
            files={"workspace/skills/foo.md": "# Skill"},
        )
        report = er._evaluate_quality(mutation, {})
        assert report is None
