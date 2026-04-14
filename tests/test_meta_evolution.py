"""Tests for app.meta_evolution — meta-evolution loop.

Covers Phase 5: the engine that evolves the evolution engine's own parameters.
Tests effectiveness measurement, improvement detection, rate limiting, and
the full meta-evolution cycle with mocked dependencies.
"""
import os
import sys
import json
import time
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.test_metrics import _FakeSettings
import app.config as config_mod
config_mod.get_settings = lambda: _FakeSettings()
config_mod.get_anthropic_api_key = lambda: "fake-key"
config_mod.get_gateway_secret = lambda: "a" * 64


# ── Effectiveness measurement ───────────────────────────────────────────────

class TestMeasureEvolutionEffectiveness:
    @patch("app.results_ledger.get_recent_results", return_value=[])
    def test_empty_results(self, mock_results):
        from app.meta_evolution import measure_evolution_effectiveness
        m = measure_evolution_effectiveness()
        assert m["kept_ratio"] == 0.0
        assert m["sample_size"] == 0

    @patch("app.results_ledger.get_recent_results")
    def test_mixed_results(self, mock_results):
        mock_results.return_value = [
            {"status": "keep", "delta": 0.05, "change_type": "code", "hypothesis": "fix bug A"},
            {"status": "keep", "delta": 0.02, "change_type": "skill", "hypothesis": "add skill B"},
            {"status": "discard", "delta": -0.01, "change_type": "code", "hypothesis": "try thing C"},
            {"status": "discard", "delta": -0.03, "change_type": "skill", "hypothesis": "improve D"},
            {"status": "keep", "delta": 0.01, "change_type": "code", "hypothesis": "optimize E"},
        ]
        from app.meta_evolution import measure_evolution_effectiveness
        m = measure_evolution_effectiveness(5)
        assert m["kept_ratio"] == 0.6  # 3 out of 5
        assert m["code_ratio"] == 0.6  # 3 code out of 5
        assert m["sample_size"] == 5
        assert m["avg_delta"] > 0

    @patch("app.results_ledger.get_recent_results", side_effect=Exception("DB down"))
    def test_exception_returns_defaults(self, mock_results):
        from app.meta_evolution import measure_evolution_effectiveness
        m = measure_evolution_effectiveness()
        assert m["kept_ratio"] == 0.0
        assert m["sample_size"] == 0


# ── Improvement detection ───────────────────────────────────────────────────

class TestImprovementDetection:
    def test_closer_to_ideal_kept_ratio_is_improvement(self):
        from app.meta_evolution import _is_improvement
        baseline = {"kept_ratio": 0.80, "avg_delta": 0.01, "diversity": 0.3, "code_ratio": 0.5}
        current = {"kept_ratio": 0.45, "avg_delta": 0.01, "diversity": 0.3, "code_ratio": 0.5}
        assert _is_improvement(baseline, current) is True

    def test_away_from_ideal_is_not_improvement(self):
        from app.meta_evolution import _is_improvement
        baseline = {"kept_ratio": 0.40, "avg_delta": 0.01, "diversity": 0.3, "code_ratio": 0.5}
        current = {"kept_ratio": 0.90, "avg_delta": 0.01, "diversity": 0.3, "code_ratio": 0.5}
        assert _is_improvement(baseline, current) is False

    def test_higher_avg_delta_is_improvement(self):
        from app.meta_evolution import _is_improvement
        baseline = {"kept_ratio": 0.40, "avg_delta": 0.001, "diversity": 0.3, "code_ratio": 0.5}
        current = {"kept_ratio": 0.40, "avg_delta": 0.05, "diversity": 0.3, "code_ratio": 0.5}
        assert _is_improvement(baseline, current) is True


class TestEffectivenessScore:
    def test_peaks_at_ideal_values(self):
        from app.meta_evolution import _effectiveness_score
        ideal = {"kept_ratio": 0.40, "avg_delta": 0.10, "diversity": 0.50, "code_ratio": 0.60}
        poor = {"kept_ratio": 0.90, "avg_delta": 0.0, "diversity": 0.10, "code_ratio": 0.10}
        assert _effectiveness_score(ideal) > _effectiveness_score(poor)

    def test_score_in_valid_range(self):
        from app.meta_evolution import _effectiveness_score
        for kr in [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]:
            score = _effectiveness_score({
                "kept_ratio": kr, "avg_delta": 0.01,
                "diversity": 0.3, "code_ratio": 0.5,
            })
            assert 0.0 <= score <= 1.0, f"Score {score} out of range for kr={kr}"


# ── History management ──────────────────────────────────────────────────────

class TestHistoryManagement:
    def test_save_and_load_round_trip(self, tmp_path, monkeypatch):
        import app.meta_evolution as mod
        monkeypatch.setattr(mod, "META_HISTORY_PATH", tmp_path / "history.json")

        history = [{"status": "completed", "timestamp": time.time(), "promoted": True}]
        mod._save_history(history)
        loaded = mod._load_history()
        assert len(loaded) == 1
        assert loaded[0]["promoted"] is True

    def test_load_missing_file_returns_empty(self, tmp_path, monkeypatch):
        import app.meta_evolution as mod
        monkeypatch.setattr(mod, "META_HISTORY_PATH", tmp_path / "nonexistent.json")
        assert mod._load_history() == []

    def test_count_weekly_mutations(self, tmp_path, monkeypatch):
        import app.meta_evolution as mod
        monkeypatch.setattr(mod, "META_HISTORY_PATH", tmp_path / "history.json")

        now = time.time()
        history = [
            {"promoted": True, "timestamp": now - 3600},       # 1h ago — counts
            {"promoted": True, "timestamp": now - 86400},      # 1d ago — counts
            {"promoted": False, "timestamp": now - 3600},      # not promoted — skip
            {"promoted": True, "timestamp": now - 8 * 86400},  # 8d ago — skip
        ]
        mod._save_history(history)
        assert mod._count_weekly_mutations() == 2

    def test_hours_since_last_cycle(self, tmp_path, monkeypatch):
        import app.meta_evolution as mod
        monkeypatch.setattr(mod, "META_HISTORY_PATH", tmp_path / "history.json")

        now = time.time()
        history = [{"timestamp": now - 7200}]  # 2 hours ago
        mod._save_history(history)
        hours = mod._hours_since_last_cycle()
        assert 1.9 < hours < 2.1

    def test_hours_since_no_history(self, tmp_path, monkeypatch):
        import app.meta_evolution as mod
        monkeypatch.setattr(mod, "META_HISTORY_PATH", tmp_path / "nonexistent.json")
        assert mod._hours_since_last_cycle() == float("inf")


# ── Rate limiting ───────────────────────────────────────────────────────────

class TestMetaEvolutionRateLimiting:
    def test_skips_on_weekly_limit(self, tmp_path, monkeypatch):
        import app.meta_evolution as mod
        monkeypatch.setattr(mod, "META_HISTORY_PATH", tmp_path / "history.json")
        monkeypatch.setattr(mod, "META_DIR", tmp_path / "meta")
        (tmp_path / "meta").mkdir()
        (tmp_path / "meta" / "test.json").write_text("{}")

        now = time.time()
        history = [
            {"promoted": True, "timestamp": now - 3600},
            {"promoted": True, "timestamp": now - 7200},
            {"promoted": True, "timestamp": now - 10800},
        ]
        mod._save_history(history)

        result = mod.run_meta_evolution_cycle()
        assert result["status"] == "skipped"
        assert "Weekly limit" in result["reason"]

    def test_skips_on_cooldown(self, tmp_path, monkeypatch):
        import app.meta_evolution as mod
        monkeypatch.setattr(mod, "META_HISTORY_PATH", tmp_path / "history.json")
        monkeypatch.setattr(mod, "META_DIR", tmp_path / "meta")
        (tmp_path / "meta").mkdir()
        (tmp_path / "meta" / "test.json").write_text("{}")

        history = [{"promoted": False, "timestamp": time.time() - 3600}]  # 1h ago, cooldown 8h
        mod._save_history(history)

        result = mod.run_meta_evolution_cycle()
        assert result["status"] == "skipped"
        assert "Cooldown" in result["reason"]

    def test_skips_no_meta_files(self, tmp_path, monkeypatch):
        import app.meta_evolution as mod
        monkeypatch.setattr(mod, "META_HISTORY_PATH", tmp_path / "history.json")
        monkeypatch.setattr(mod, "META_DIR", tmp_path / "empty_meta")

        result = mod.run_meta_evolution_cycle()
        assert result["status"] == "skipped"
        assert "No meta-parameter" in result["reason"]

    @patch("app.results_ledger.get_recent_results", return_value=[{"status": "keep"}] * 5)
    def test_skips_insufficient_data(self, mock_results, tmp_path, monkeypatch):
        import app.meta_evolution as mod
        monkeypatch.setattr(mod, "META_HISTORY_PATH", tmp_path / "history.json")
        monkeypatch.setattr(mod, "META_DIR", tmp_path / "meta")
        (tmp_path / "meta").mkdir()
        (tmp_path / "meta" / "test.json").write_text("{}")

        result = mod.run_meta_evolution_cycle()
        assert result["status"] == "skipped"
        assert "Insufficient" in result["reason"]


# ── Meta-parameter file operations ──────────────────────────────────────────

class TestMetaFileOperations:
    def test_load_meta_files(self, tmp_path, monkeypatch):
        import app.meta_evolution as mod
        monkeypatch.setattr(mod, "META_DIR", tmp_path)
        (tmp_path / "weights.json").write_text('{"a": 1}')
        (tmp_path / "prompt.md").write_text("# Prompt")
        (tmp_path / "not_meta.txt").write_text("skip")  # .txt not loaded

        files = mod._load_meta_files()
        assert "weights.json" in files
        assert "prompt.md" in files
        assert "not_meta.txt" not in files

    def test_backup_and_restore(self, tmp_path, monkeypatch):
        import app.meta_evolution as mod
        monkeypatch.setattr(mod, "META_DIR", tmp_path / "meta")
        monkeypatch.setattr(mod, "META_BACKUP_DIR", tmp_path / "backups")
        (tmp_path / "meta").mkdir()

        original = '{"weight": 0.30}'
        (tmp_path / "meta" / "weights.json").write_text(original)

        backup_path = mod._backup_meta_file("weights.json")
        assert backup_path is not None
        assert backup_path.exists()

        # Modify original
        (tmp_path / "meta" / "weights.json").write_text('{"weight": 0.50}')

        # Restore
        assert mod._restore_meta_file("weights.json", backup_path) is True
        assert (tmp_path / "meta" / "weights.json").read_text() == original


# ── Entry point ─────────────────────────────────────────────────────────────

class TestRunMetaEvolution:
    def test_entry_point_never_crashes(self, tmp_path, monkeypatch):
        """run_meta_evolution() wraps the cycle and should never raise."""
        import app.meta_evolution as mod
        monkeypatch.setattr(mod, "META_HISTORY_PATH", tmp_path / "history.json")
        monkeypatch.setattr(mod, "META_DIR", tmp_path / "empty")
        # Should complete without exception
        mod.run_meta_evolution()
