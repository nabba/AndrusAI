"""
LLM Discovery Pipeline Tests
==============================

Tests the full model discovery → benchmark → promotion pipeline.

Run: docker exec crewai-team-gateway-1 python3 -m pytest /app/tests/test_llm_discovery.py -v
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ════════════════════════════════════════════════════════════════════════════════
# 1. IMPORT TESTS
# ════════════════════════════════════════════════════════════════════════════════

class TestImports:
    def test_module_imports(self):
        from app.llm_discovery import (
            scan_openrouter, scan_ollama, run_discovery_cycle,
            benchmark_model, propose_promotion, get_discovered_models,
            format_discovery_report,
        )
        assert callable(run_discovery_cycle)
        assert callable(benchmark_model)

    def test_idle_scheduler_has_discovery_job(self):
        from app.idle_scheduler import _default_jobs
        jobs = _default_jobs()
        # _default_jobs returns (name, fn, JobWeight) tuples
        job_names = [entry[0] for entry in jobs]
        assert "llm-discovery" in job_names

    def test_signal_commands_wired(self):
        from app.agents.commander.commands import try_command
        # These should not crash (may return error if no DB)
        result = try_command("discovered models", "test", None)
        assert result is not None


# ════════════════════════════════════════════════════════════════════════════════
# 2. NORMALIZATION TESTS
# ════════════════════════════════════════════════════════════════════════════════

class TestNormalization:
    def test_normalize_standard_model(self):
        from app.llm_discovery import _normalize_model
        raw = {
            "id": "google/gemma-4-31b-it",
            "name": "Gemma 4 31B",
            "context_length": 256000,
            "pricing": {"prompt": "0.00000014", "completion": "0.0000004"},
            "architecture": {"modality": "text+image"},
        }
        result = _normalize_model(raw, "openrouter")
        assert result is not None
        assert result["model_id"] == "openrouter/google/gemma-4-31b-it"
        assert result["display_name"] == "Gemma 4 31B"
        assert result["context_window"] == 256000
        assert result["multimodal"] is True
        assert result["cost_output_per_m"] == pytest.approx(0.4, abs=0.01)

    def test_normalize_free_model(self):
        from app.llm_discovery import _normalize_model
        raw = {
            "id": "nvidia/nemotron-nano-12b:free",
            "name": "Nemotron Nano 12B (free)",
            "context_length": 32768,
            "pricing": {"prompt": "0", "completion": "0"},
            "architecture": {"modality": "text"},
        }
        result = _normalize_model(raw, "openrouter")
        assert result is not None
        assert result["tier"] == "free"
        assert result["cost_output_per_m"] == 0.0

    def test_filter_small_context(self):
        from app.llm_discovery import _normalize_model
        raw = {
            "id": "tiny-model",
            "name": "Tiny",
            "context_length": 2048,  # Below MIN_CONTEXT_WINDOW
            "pricing": {"prompt": "0", "completion": "0"},
            "architecture": {"modality": "text"},
        }
        result = _normalize_model(raw, "openrouter")
        assert result is None  # Filtered out

    def test_filter_expensive(self):
        from app.llm_discovery import _normalize_model
        raw = {
            "id": "ultra-expensive",
            "name": "Ultra",
            "context_length": 100000,
            "pricing": {"prompt": "0.0001", "completion": "0.0001"},  # $100/M
            "architecture": {"modality": "text"},
        }
        result = _normalize_model(raw, "openrouter")
        assert result is None  # Filtered out (too expensive)

    def test_tier_classification(self):
        from app.llm_discovery import _normalize_model

        # Free
        r = _normalize_model({"id": "f", "name": "F", "context_length": 10000,
            "pricing": {"prompt": "0", "completion": "0"}, "architecture": {"modality": "text"}}, "openrouter")
        assert r["tier"] == "free"

        # Budget
        r = _normalize_model({"id": "b", "name": "B", "context_length": 10000,
            "pricing": {"prompt": "0", "completion": "0.0000005"}, "architecture": {"modality": "text"}}, "openrouter")
        assert r["tier"] == "budget"


# ════════════════════════════════════════════════════════════════════════════════
# 3. DATABASE TESTS
# ════════════════════════════════════════════════════════════════════════════════

class TestDatabase:
    def test_store_and_retrieve(self):
        """Store a model and retrieve it."""
        from app.llm_discovery import _store_discovered, get_discovered_models
        model = {
            "model_id": "test/unit-test-model",
            "provider": "test",
            "display_name": "Unit Test Model",
            "context_window": 32768,
            "cost_input_per_m": 0.1,
            "cost_output_per_m": 0.3,
            "multimodal": False,
            "tool_calling": True,
            "tier": "budget",
            "raw_metadata": {},
        }
        stored = _store_discovered(model)
        # May fail without DB — that's OK
        if stored:
            # Retrieve all models (may be 300+) and search for our test model
            models = get_discovered_models(limit=500)
            found = any(m.get("model_id") == "test/unit-test-model" for m in models)
            # If not found in top 500 by date, it may be too old — check directly
            if not found:
                from app.control_plane.db import execute
                direct = execute(
                    "SELECT model_id FROM control_plane.discovered_models WHERE model_id = %s",
                    ("test/unit-test-model",), fetch=True,
                )
                found = bool(direct)
            assert found

    def test_get_known_model_ids(self):
        from app.llm_discovery import _get_known_model_ids
        ids = _get_known_model_ids()
        assert isinstance(ids, set)

    def test_get_catalog_model_ids(self):
        from app.llm_discovery import _get_catalog_model_ids
        ids = _get_catalog_model_ids()
        assert len(ids) > 0  # Should have static catalog models


# ════════════════════════════════════════════════════════════════════════════════
# 4. PIPELINE LOGIC TESTS
# ════════════════════════════════════════════════════════════════════════════════

class TestPipelineLogic:
    def test_runtime_catalog_injection(self):
        """Promoted models should appear in the runtime catalog."""
        from app.llm_discovery import _add_to_runtime_catalog
        from app.llm_catalog import CATALOG

        model = {
            "model_id": "openrouter/test/discovery-test-model",
            "provider": "openrouter",
            "display_name": "Discovery Test Model",
            "context_window": 64000,
            "cost_input_per_m": 0.5,
            "cost_output_per_m": 1.0,
            "multimodal": False,
            "tier": "budget",
            "benchmark_score": 0.75,
        }

        _add_to_runtime_catalog(model, ["research", "coding"])

        # Should now be in the catalog
        assert "discovery-test-model" in CATALOG
        entry = CATALOG["discovery-test-model"]
        assert entry["tier"] == "budget"
        assert entry["_discovered"] is True
        assert entry["strengths"]["research"] == 0.75
        assert entry["strengths"]["coding"] == 0.75

        # Clean up
        del CATALOG["discovery-test-model"]

    def test_tier_classification_boundaries(self):
        from app.llm_discovery import _normalize_model

        # Exactly at budget boundary ($1/M)
        r = _normalize_model({"id": "x", "name": "X", "context_length": 10000,
            "pricing": {"prompt": "0", "completion": "0.000001"}, "architecture": {"modality": "text"}}, "openrouter")
        assert r["tier"] == "budget"

    def test_discovery_cycle_returns_dict(self):
        """run_discovery_cycle should always return a result dict."""
        from app.llm_discovery import run_discovery_cycle
        # With no API key, should return early
        result = run_discovery_cycle(max_benchmarks=0)
        assert isinstance(result, dict)
        assert "scanned" in result
        assert "new_found" in result

    def test_format_report_no_crash(self):
        from app.llm_discovery import format_discovery_report
        report = format_discovery_report()
        assert isinstance(report, str)
        assert "Discovery" in report or "discovered" in report.lower()


# ════════════════════════════════════════════════════════════════════════════════
# 5. INTEGRATION TESTS (require OpenRouter API key)
# ════════════════════════════════════════════════════════════════════════════════

class TestIntegration:
    def test_openrouter_scan_returns_models(self):
        """If API key is set, scan should return models."""
        from app.llm_discovery import scan_openrouter
        models = scan_openrouter()
        # May be empty without API key — that's OK
        if models:
            assert len(models) > 10  # OpenRouter has 100+ models
            # Check structure
            m = models[0]
            assert "id" in m
            assert "pricing" in m or "context_length" in m

    def test_full_discovery_cycle(self):
        """Full cycle should complete without errors."""
        from app.llm_discovery import run_discovery_cycle
        result = run_discovery_cycle(max_benchmarks=1)
        assert isinstance(result, dict)


# ════════════════════════════════════════════════════════════════════════════════
# 6. PHASE 2: MULTI-ROLE BENCHMARK + TOOL DETECTION + GOVERNANCE CONSUMER
# ════════════════════════════════════════════════════════════════════════════════


class TestMultiRoleBenchmark:
    def test_benchmark_all_roles_returns_dict(self):
        """_benchmark_all_roles returns a dict keyed by role."""
        from app.llm_discovery import _benchmark_all_roles, BENCHMARK_ROLES

        with patch("app.llm_discovery.benchmark_model",
                   side_effect=[0.8, 0.6, 0.7]):
            scores = _benchmark_all_roles("openrouter/some-model")
            assert set(scores.keys()) == set(BENCHMARK_ROLES)
            assert scores["research"] == 0.8
            assert scores["coding"] == 0.6
            assert scores["writing"] == 0.7

    def test_benchmark_skip_on_negative_score(self):
        """benchmark_model returning -1 for a role drops it from the map."""
        from app.llm_discovery import _benchmark_all_roles

        with patch("app.llm_discovery.benchmark_model",
                   side_effect=[0.8, -1.0, 0.7]):
            scores = _benchmark_all_roles("m")
            assert "coding" not in scores
            assert scores["research"] == 0.8
            assert scores["writing"] == 0.7


class TestGovernanceConsumer:
    def test_returns_summary_when_db_empty(self):
        from app.llm_discovery import consume_approved_promotions

        with patch("app.control_plane.db.execute", return_value=[]):
            summary = consume_approved_promotions()
            assert summary == {"applied": 0, "skipped": 0, "errors": 0}

    def test_skips_without_model_id(self):
        from app.llm_discovery import consume_approved_promotions

        rows = [{"id": "req-1", "detail_json": {"role": "coding"},
                 "reviewed_at": None, "reviewed_by": "user"}]

        with patch("app.control_plane.db.execute", return_value=rows):
            summary = consume_approved_promotions()
            assert summary["skipped"] == 1
            assert summary["applied"] == 0

    def test_applies_and_marks_consumed(self):
        """A complete approved request triggers catalog add + DB update."""
        from app.llm_discovery import consume_approved_promotions

        approved = [{
            "id": "req-42",
            "detail_json": {
                "model_id": "openrouter/new/model-x",
                "role": "coding",
                "tier": "budget",
            },
            "reviewed_at": None,
            "reviewed_by": "user",
        }]
        disc_row = [{
            "model_id": "openrouter/new/model-x",
            "provider": "openrouter",
            "display_name": "Model X",
            "context_window": 128_000,
            "cost_input_per_m": 0.2,
            "cost_output_per_m": 0.6,
            "multimodal": False,
            "tool_calling": True,
            "benchmark_score": 0.85,
        }]
        # First execute() returns the approved request list.
        # Second returns the discovered_models lookup.
        # Remaining UPDATE statements return [] (fetch=False path).
        call_outputs = iter([approved, disc_row, [], []])

        def _fake_execute(q, params=(), fetch=False):
            out = next(call_outputs)
            return out if fetch else []

        with (patch("app.control_plane.db.execute", side_effect=_fake_execute),
              patch("app.llm_discovery._add_to_runtime_catalog") as add_mock,
              patch("app.llm_discovery._promote_model") as promote_mock):
            summary = consume_approved_promotions()

        assert summary["applied"] == 1
        add_mock.assert_called_once()
        promote_mock.assert_called_once_with(
            "openrouter/new/model-x", "budget", ["coding"], reviewer="user",
        )


class TestRunDiscoveryCycleCallsScanOllama:
    def test_cycle_invokes_scan_ollama(self):
        """Ollama scan is no longer dead code."""
        import app.llm_discovery as d

        with (patch.object(d, "scan_openrouter", return_value=[]) as sor,
              patch.object(d, "scan_ollama", return_value=[]) as som):
            result = d.run_discovery_cycle(max_benchmarks=0)
            sor.assert_called_once()
            som.assert_called_once()
            assert result["scanned"] == 0


class TestRehydrate:
    def test_rehydrate_idempotent(self):
        """Second call is a no-op when already rehydrated."""
        import app.llm_rehydrate as r

        r._rehydrated = True
        added = r.rehydrate_catalog()
        assert added == 0

    def test_rehydrate_replays_promoted_rows(self):
        import app.llm_rehydrate as r

        rows = [{
            "model_id": "openrouter/rehydrated/model",
            "provider": "openrouter",
            "display_name": "Rehydrated",
            "context_window": 128_000,
            "cost_input_per_m": 0.1,
            "cost_output_per_m": 0.4,
            "multimodal": False,
            "tool_calling": True,
            "promoted_tier": "budget",
            "promoted_roles": ["research"],
            "benchmark_score": 0.7,
            "benchmark_role": "research",
        }]
        r._rehydrated = False

        with (patch("app.control_plane.db.execute", return_value=rows),
              patch("app.llm_discovery._add_to_runtime_catalog") as add_mock):
            added = r.rehydrate_catalog(force=True)
            assert added == 1
            add_mock.assert_called_once()
            payload = add_mock.call_args[0][0]
            assert payload["model_id"] == "openrouter/rehydrated/model"
            assert payload["tier"] == "budget"


# ════════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
