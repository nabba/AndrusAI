"""Tests for app.external_benchmarks — external benchmark integration.

Covers Phase 6: GAIA-style external benchmarks for Goodhart's Law prevention.
Tests caching, thread safety, and benchmark execution with mocked LLM calls.
"""
import os
import sys
import time
import threading
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.test_metrics import _FakeSettings
import app.config as config_mod
config_mod.get_settings = lambda: _FakeSettings()
config_mod.get_anthropic_api_key = lambda: "fake-key"
config_mod.get_gateway_secret = lambda: "a" * 64


class TestBenchmarkTasks:
    """Verify the benchmark task suite structure."""

    def test_benchmark_tasks_exist(self):
        from app.external_benchmarks import _BENCHMARK_TASKS
        assert len(_BENCHMARK_TASKS) >= 50, f"Expected 50+ tasks, got {len(_BENCHMARK_TASKS)}"

    def test_all_tasks_have_required_fields(self):
        from app.external_benchmarks import _BENCHMARK_TASKS
        required = {"task", "category", "difficulty", "validation"}
        for i, t in enumerate(_BENCHMARK_TASKS):
            missing = required - set(t.keys())
            assert not missing, f"Task {i} missing: {missing}"

    def test_categories_diverse(self):
        from app.external_benchmarks import _BENCHMARK_TASKS
        categories = set(t["category"] for t in _BENCHMARK_TASKS)
        assert "coding" in categories
        assert "research" in categories
        assert "reasoning" in categories or "multi_step_reasoning" in categories
        assert "safety" in categories or "safety_boundary" in categories

    def test_validation_types_present(self):
        from app.external_benchmarks import _BENCHMARK_TASKS
        types_found = set()
        for t in _BENCHMARK_TASKS:
            rule = t.get("validation", "")
            prefix = rule.split(":")[0] if ":" in rule else "other"
            types_found.add(prefix)
        assert "exec_passes" in types_found, "No exec_passes tasks in benchmarks"
        assert "judge" in types_found, "No judge tasks in benchmarks"

    def test_difficulty_range(self):
        from app.external_benchmarks import _BENCHMARK_TASKS
        difficulties = [t["difficulty"] for t in _BENCHMARK_TASKS]
        assert min(difficulties) >= 1
        assert max(difficulties) <= 5
        # Benchmarks should be harder than average
        avg = sum(difficulties) / len(difficulties)
        assert avg >= 3.5, f"Average difficulty {avg:.1f} too low for external benchmarks"


class TestRunExternalBenchmark:
    """Tests for run_external_benchmark() with mocked LLM."""

    def setup_method(self):
        """Clear cache before each test."""
        import app.external_benchmarks as mod
        mod._benchmark_cache.clear()

    @patch("app.experiment_runner.validate_response", return_value=True)
    @patch("app.llm_factory.create_specialist_llm")
    def test_returns_float_in_valid_range(self, mock_llm_factory, mock_validate):
        mock_llm = MagicMock()
        mock_llm.call.return_value = "test response"
        mock_llm_factory.return_value = mock_llm

        from app.external_benchmarks import run_external_benchmark
        score = run_external_benchmark(sample_size=5)
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    @patch("app.experiment_runner.validate_response", return_value=True)
    @patch("app.llm_factory.create_specialist_llm")
    def test_caches_result(self, mock_llm_factory, mock_validate):
        mock_llm = MagicMock()
        mock_llm.call.return_value = "response"
        mock_llm_factory.return_value = mock_llm

        from app.external_benchmarks import run_external_benchmark
        score1 = run_external_benchmark(sample_size=3)
        score2 = run_external_benchmark(sample_size=3)
        # Second call should use cache — same score, LLM called only once
        assert score1 == score2
        # Factory may be called once (first run only)
        assert mock_llm_factory.call_count == 1

    @patch("app.experiment_runner.validate_response", return_value=True)
    @patch("app.llm_factory.create_specialist_llm")
    def test_cache_expires(self, mock_llm_factory, mock_validate):
        mock_llm = MagicMock()
        mock_llm.call.return_value = "response"
        mock_llm_factory.return_value = mock_llm

        import app.external_benchmarks as mod
        # Run once to populate cache
        mod.run_external_benchmark(sample_size=3)

        # Manually expire the cache
        if mod._benchmark_cache:
            for key in mod._benchmark_cache:
                mod._benchmark_cache[key]["timestamp"] = time.time() - 7200  # 2h ago

        # Should re-run
        mod.run_external_benchmark(sample_size=3)
        assert mock_llm_factory.call_count == 2


class TestGetCachedBenchmarkScore:
    def setup_method(self):
        import app.external_benchmarks as mod
        mod._benchmark_cache.clear()

    def test_returns_none_when_no_cache(self):
        from app.external_benchmarks import get_cached_benchmark_score
        assert get_cached_benchmark_score() is None

    @patch("app.experiment_runner.validate_response", return_value=True)
    @patch("app.llm_factory.create_specialist_llm")
    def test_returns_score_when_cached(self, mock_llm_factory, mock_validate):
        mock_llm = MagicMock()
        mock_llm.call.return_value = "response"
        mock_llm_factory.return_value = mock_llm

        from app.external_benchmarks import run_external_benchmark, get_cached_benchmark_score
        run_external_benchmark(sample_size=3)
        score = get_cached_benchmark_score()
        assert score is not None
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0


class TestGetBenchmarkStats:
    @patch("app.experiment_runner.validate_response", return_value=True)
    @patch("app.llm_factory.create_specialist_llm")
    def test_returns_category_breakdown(self, mock_llm_factory, mock_validate):
        mock_llm = MagicMock()
        mock_llm.call.return_value = "response"
        mock_llm_factory.return_value = mock_llm

        from app.external_benchmarks import run_external_benchmark, get_benchmark_stats
        run_external_benchmark(sample_size=5)
        stats = get_benchmark_stats()
        assert isinstance(stats, dict)


class TestThreadSafety:
    """Verify concurrent access to the benchmark cache doesn't cause corruption."""

    @patch("app.experiment_runner.validate_response", return_value=True)
    @patch("app.llm_factory.create_specialist_llm")
    def test_concurrent_calls(self, mock_llm_factory, mock_validate):
        mock_llm = MagicMock()
        mock_llm.call.return_value = "response"
        mock_llm_factory.return_value = mock_llm

        import app.external_benchmarks as mod
        mod._benchmark_cache.clear()

        errors = []
        results = []

        def run_benchmark():
            try:
                score = mod.run_external_benchmark(sample_size=3)
                results.append(score)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=run_benchmark) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Concurrent benchmark errors: {errors}"
        assert len(results) == 5
        # All should get the same cached value
        assert len(set(results)) == 1
