"""Tests for meta-parameter extraction and runtime loading.

Covers Phase 4: workspace/meta/ files loaded at runtime with fallback to hardcoded defaults.
Tests avo_operator._load_meta_prompt(), metrics._load_composite_weights(), and
adaptive_ensemble._load_phase_weights().
"""
import os
import sys
import json
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.test_metrics import _FakeSettings
import app.config as config_mod
config_mod.get_settings = lambda: _FakeSettings()
config_mod.get_anthropic_api_key = lambda: "fake-key"
config_mod.get_gateway_secret = lambda: "a" * 64


# ── AVO Operator: _load_meta_prompt ─────────────────────────────────────────

class TestLoadMetaPrompt:
    def test_returns_file_content_when_exists(self, tmp_path, monkeypatch):
        import app.avo_operator as avo
        monkeypatch.setattr(avo, "_META_DIR", tmp_path)
        (tmp_path / "test_prompt.md").write_text("# Custom Prompt\nDo this.")

        result = avo._load_meta_prompt("test_prompt.md", "fallback")
        assert result == "# Custom Prompt\nDo this."

    def test_returns_fallback_when_missing(self, tmp_path, monkeypatch):
        import app.avo_operator as avo
        monkeypatch.setattr(avo, "_META_DIR", tmp_path)

        result = avo._load_meta_prompt("nonexistent.md", "fallback text")
        assert result == "fallback text"

    def test_returns_fallback_on_empty_file(self, tmp_path, monkeypatch):
        import app.avo_operator as avo
        monkeypatch.setattr(avo, "_META_DIR", tmp_path)
        (tmp_path / "empty.md").write_text("")

        result = avo._load_meta_prompt("empty.md", "fallback")
        assert result == "fallback"

    def test_returns_fallback_on_read_error(self, tmp_path, monkeypatch):
        import app.avo_operator as avo
        monkeypatch.setattr(avo, "_META_DIR", tmp_path / "nonexistent_dir")

        result = avo._load_meta_prompt("file.md", "safe fallback")
        assert result == "safe fallback"


# ── Metrics: _load_composite_weights ────────────────────────────────────────

class TestLoadCompositeWeights:
    def test_loads_from_meta_file(self, tmp_path, monkeypatch):
        # Write a custom weights file
        meta_dir = tmp_path / "workspace" / "meta"
        meta_dir.mkdir(parents=True)
        weights = {
            "task_success_rate": 0.25,
            "error_score": 0.15,
            "self_heal_rate": 0.10,
            "output_quality": 0.15,
            "error_resolution": 0.10,
            "response_time": 0.10,
            "external_benchmark": 0.15,
        }
        (meta_dir / "composite_weights.json").write_text(json.dumps(weights))

        from app.metrics import _load_composite_weights
        import app.metrics as metrics_mod
        # Monkeypatch the path inside the function (it uses hardcoded /app/workspace/meta/)
        from unittest.mock import patch
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value=json.dumps(weights)):
            result = _load_composite_weights()
            assert "task_success_rate" in result
            assert result["task_success_rate"] == 0.25

    def test_defaults_when_file_missing(self):
        from app.metrics import _load_composite_weights
        from unittest.mock import patch
        with patch("pathlib.Path.exists", return_value=False):
            result = _load_composite_weights()
            assert result["task_success_rate"] == 0.30
            assert result["error_score"] == 0.20

    def test_excludes_underscore_keys(self):
        from app.metrics import _load_composite_weights
        from unittest.mock import patch
        data = {"_meta": "info", "task_success_rate": 0.30, "error_score": 0.20}
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value=json.dumps(data)):
            result = _load_composite_weights()
            assert "_meta" not in result

    def test_handles_invalid_json(self):
        from app.metrics import _load_composite_weights
        from unittest.mock import patch
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value="not valid json"):
            result = _load_composite_weights()
            # Should fall back to defaults
            assert result["task_success_rate"] == 0.30


class TestCompositeScoreWithExternalBenchmark:
    """Test that composite_score() integrates external benchmark when configured."""

    def test_score_in_valid_range(self):
        from app.metrics import composite_score
        score = composite_score()
        assert 0.0 <= score <= 1.0

    def test_score_without_external_benchmark(self):
        """When no external benchmark weight is set, score uses original weights."""
        from app.metrics import composite_score, _load_composite_weights
        from unittest.mock import patch
        defaults = {
            "task_success_rate": 0.30, "error_score": 0.20,
            "self_heal_rate": 0.15, "output_quality": 0.15,
            "error_resolution": 0.10, "response_time": 0.10,
        }
        with patch("app.metrics._load_composite_weights", return_value=defaults):
            score = composite_score()
            assert 0.0 <= score <= 1.0

    def test_score_with_external_benchmark(self):
        """When external_benchmark weight is set, score includes it."""
        from app.metrics import composite_score
        from unittest.mock import patch
        weights_with_ext = {
            "task_success_rate": 0.25, "error_score": 0.15,
            "self_heal_rate": 0.10, "output_quality": 0.15,
            "error_resolution": 0.10, "response_time": 0.10,
            "external_benchmark": 0.15,
        }
        with patch("app.metrics._load_composite_weights", return_value=weights_with_ext), \
             patch("app.external_benchmarks.get_cached_benchmark_score", return_value=0.80):
            score = composite_score()
            assert 0.0 <= score <= 1.0


# ── Adaptive Ensemble: _load_phase_weights ──────────────────────────────────

class TestLoadPhaseWeights:
    def test_phase_weights_has_required_phases(self):
        from app.adaptive_ensemble import PHASE_WEIGHTS
        assert "exploration" in PHASE_WEIGHTS
        assert "exploitation" in PHASE_WEIGHTS
        assert "evaluation" in PHASE_WEIGHTS

    def test_phase_weights_have_valid_tiers(self):
        from app.adaptive_ensemble import PHASE_WEIGHTS
        for phase, weights in PHASE_WEIGHTS.items():
            for tier, prob in weights.items():
                assert 0.0 <= prob <= 1.0, f"{phase}.{tier} = {prob} not in [0, 1]"

    def test_phase_weights_sum_to_one(self):
        from app.adaptive_ensemble import PHASE_WEIGHTS
        for phase, weights in PHASE_WEIGHTS.items():
            total = sum(weights.values())
            assert abs(total - 1.0) < 0.01, f"{phase} weights sum to {total}, not 1.0"

    def test_load_phase_weights_with_custom_file(self):
        from app.adaptive_ensemble import _load_phase_weights
        from unittest.mock import patch
        custom = {
            "exploration": {"local": 0.50, "budget": 0.30, "mid": 0.20, "premium": 0.00},
            "exploitation": {"local": 0.00, "budget": 0.10, "mid": 0.40, "premium": 0.50},
        }
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value=json.dumps(custom)):
            result = _load_phase_weights()
            assert result["exploration"]["local"] == 0.50

    def test_load_phase_weights_fallback_on_invalid(self):
        from app.adaptive_ensemble import _load_phase_weights, _DEFAULT_PHASE_WEIGHTS
        from unittest.mock import patch
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value="invalid json"):
            result = _load_phase_weights()
            assert result == _DEFAULT_PHASE_WEIGHTS


# ── workspace/meta/ files structural validation ─────────────────────────────

class TestMetaFilesExist:
    """Verify that all expected workspace/meta/ files exist and are valid."""

    META_DIR = os.path.join(os.path.dirname(__file__), "..", "workspace", "meta")

    def test_all_meta_files_present(self):
        expected = [
            "avo_planning_prompt.md", "avo_critique_prompt.md",
            "composite_weights.json", "ensemble_weights.json",
            "judge_rubric.json", "selection_criteria.json",
        ]
        for f in expected:
            path = os.path.join(self.META_DIR, f)
            assert os.path.exists(path), f"Missing meta file: workspace/meta/{f}"

    def test_json_files_valid(self):
        json_files = [
            "composite_weights.json", "ensemble_weights.json",
            "judge_rubric.json", "selection_criteria.json",
        ]
        for f in json_files:
            path = os.path.join(self.META_DIR, f)
            if os.path.exists(path):
                with open(path) as fh:
                    data = json.load(fh)
                assert isinstance(data, dict), f"{f} should be a JSON object"

    def test_planning_prompt_has_freeze_blocks(self):
        path = os.path.join(self.META_DIR, "avo_planning_prompt.md")
        if os.path.exists(path):
            content = open(path).read()
            assert "FREEZE-BLOCK-START" in content
            assert "EVOLVE-BLOCK-START" in content

    def test_critique_prompt_has_freeze_blocks(self):
        path = os.path.join(self.META_DIR, "avo_critique_prompt.md")
        if os.path.exists(path):
            content = open(path).read()
            assert "FREEZE-BLOCK-START" in content
            assert "EVOLVE-BLOCK-START" in content
