"""Tests for the dynamic evolution engine selector.

The audit found ShinkaEvolve had run zero times in production despite
being available — the selector's kept_ratio>0.60 gate locked the system
into AVO. The fix adds:

  1. days_since_engine_run() helper in evolution_roi
  2. Forced 7-day rotation rule placed BEFORE the kept_ratio gate
  3. ROI-aware recommendation when both engines have data

These tests verify the selector picks ShinkaEvolve under conditions where
it should, while still preferring AVO under safety-critical situations.
"""
from __future__ import annotations

import os
import sys
import time
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.test_metrics import _FakeSettings  # noqa: E402
import app.config as config_mod  # noqa: E402

config_mod.get_settings = lambda: _FakeSettings()
config_mod.get_anthropic_api_key = lambda: "fake-key"
config_mod.get_gateway_secret = lambda: "a" * 64


# ── Timestamp helper ────────────────────────────────────────────────────────

class TestEngineTimestampHelper:
    def test_no_runs_returns_zero(self, tmp_path, monkeypatch):
        import app.evolution_roi as roi
        monkeypatch.setattr(roi, "ROI_LEDGER_PATH", tmp_path / "roi.json")
        assert roi.get_last_run_timestamp("shinka") == 0.0
        assert roi.days_since_engine_run("shinka") == float("inf")

    def test_returns_most_recent(self, tmp_path, monkeypatch):
        import app.evolution_roi as roi
        monkeypatch.setattr(roi, "ROI_LEDGER_PATH", tmp_path / "roi.json")
        # Insert two runs of each engine
        roi.record_evolution_cost(
            experiment_id="avo_old", engine="avo", cost_usd=0.10,
            delta=0.01, status="keep",
        )
        # Force a tiny delay so timestamps differ
        time.sleep(0.01)
        roi.record_evolution_cost(
            experiment_id="shinka_recent", engine="shinka", cost_usd=1.00,
            delta=0.05, status="keep",
        )
        time.sleep(0.01)
        roi.record_evolution_cost(
            experiment_id="avo_recent", engine="avo", cost_usd=0.10,
            delta=0.02, status="keep",
        )
        assert roi.get_last_run_timestamp("avo") > roi.get_last_run_timestamp("shinka")

    def test_days_since_returns_positive_float(self, tmp_path, monkeypatch):
        import app.evolution_roi as roi
        monkeypatch.setattr(roi, "ROI_LEDGER_PATH", tmp_path / "roi.json")
        roi.record_evolution_cost(
            experiment_id="exp_1", engine="shinka", cost_usd=0.10,
            delta=0.05, status="keep",
        )
        days = roi.days_since_engine_run("shinka")
        assert 0.0 <= days < 1.0


# ── Selector rules ──────────────────────────────────────────────────────────

class TestSelectorManualOverride:
    def test_explicit_avo(self):
        """config.evolution_engine='avo' bypasses all logic."""
        with patch.object(_FakeSettings, "evolution_engine", "avo", create=True):
            from app.evolution import _select_evolution_engine
            assert _select_evolution_engine() == "avo"

    def test_explicit_shinka(self):
        """config.evolution_engine='shinka' bypasses all logic."""
        # Need to also mock availability or it'll fall back
        with patch.object(_FakeSettings, "evolution_engine", "shinka", create=True):
            from app.evolution import _select_evolution_engine
            # Manual override happens BEFORE availability check, so this returns "shinka"
            assert _select_evolution_engine() == "shinka"


class TestSelectorAvailability:
    def test_shinka_unavailable_returns_avo(self):
        with patch.object(_FakeSettings, "evolution_engine", "auto", create=True), \
             patch("app.evolution._is_shinka_available", return_value=False):
            from app.evolution import _select_evolution_engine
            assert _select_evolution_engine() == "avo"


class TestSelectorSafetyGate:
    def test_low_safety_returns_avo(self):
        """SUBIA safety < 0.70 forces conservative AVO."""
        with patch.object(_FakeSettings, "evolution_engine", "auto", create=True), \
             patch("app.evolution._is_shinka_available", return_value=True), \
             patch("app.evolution._get_subia_safety_value", return_value=0.50):
            from app.evolution import _select_evolution_engine
            assert _select_evolution_engine() == "avo"


class TestSelectorRotation:
    def test_forced_rotation_when_shinka_never_ran(self):
        """If shinka has never run, forced rotation picks it."""
        with patch.object(_FakeSettings, "evolution_engine", "auto", create=True), \
             patch("app.evolution._is_shinka_available", return_value=True), \
             patch("app.evolution._get_subia_safety_value", return_value=0.85), \
             patch("app.evolution.get_recent_results", return_value=[
                 {"status": "keep", "delta": 0.01} for _ in range(10)
             ]), \
             patch("app.evolution_roi.days_since_engine_run", return_value=float("inf")):
            from app.evolution import _select_evolution_engine
            assert _select_evolution_engine() == "shinka"

    def test_forced_rotation_when_shinka_run_long_ago(self):
        """If shinka ran > 7 days ago, forced rotation picks it again."""
        with patch.object(_FakeSettings, "evolution_engine", "auto", create=True), \
             patch("app.evolution._is_shinka_available", return_value=True), \
             patch("app.evolution._get_subia_safety_value", return_value=0.85), \
             patch("app.evolution.get_recent_results", return_value=[
                 {"status": "keep", "delta": 0.01} for _ in range(10)
             ]), \
             patch("app.evolution_roi.days_since_engine_run", return_value=10.0):
            from app.evolution import _select_evolution_engine
            assert _select_evolution_engine() == "shinka"

    def test_no_rotation_when_shinka_ran_recently(self):
        """If shinka ran within the last 7 days AND AVO healthy, prefer AVO."""
        recent_kept = [{"status": "keep", "delta": 0.05} for _ in range(10)]
        with patch.object(_FakeSettings, "evolution_engine", "auto", create=True), \
             patch("app.evolution._is_shinka_available", return_value=True), \
             patch("app.evolution._get_subia_safety_value", return_value=0.85), \
             patch("app.evolution.get_recent_results", return_value=recent_kept), \
             patch("app.evolution_roi.days_since_engine_run", return_value=2.0):
            from app.evolution import _select_evolution_engine
            assert _select_evolution_engine() == "avo"

    def test_stagnation_overrides_rotation(self):
        """Stagnation rule (4) should fire before rotation rule (5)."""
        all_failed = [{"status": "discard", "delta": 0.0} for _ in range(5)]
        with patch.object(_FakeSettings, "evolution_engine", "auto", create=True), \
             patch("app.evolution._is_shinka_available", return_value=True), \
             patch("app.evolution._get_subia_safety_value", return_value=0.85), \
             patch("app.evolution.get_recent_results", return_value=all_failed), \
             patch("app.evolution_roi.days_since_engine_run", return_value=0.5):
            from app.evolution import _select_evolution_engine
            # Both stagnation AND rotation point to shinka, but stagnation
            # should fire first per priority order
            assert _select_evolution_engine() == "shinka"


class TestSelectorPerformanceGates:
    def test_high_kept_ratio_returns_avo_when_shinka_recent(self):
        """When AVO is healthy AND shinka ran recently, stick with AVO."""
        kept = [{"status": "keep", "delta": 0.02} for _ in range(10)]
        with patch.object(_FakeSettings, "evolution_engine", "auto", create=True), \
             patch("app.evolution._is_shinka_available", return_value=True), \
             patch("app.evolution._get_subia_safety_value", return_value=0.85), \
             patch("app.evolution.get_recent_results", return_value=kept), \
             patch("app.evolution_roi.days_since_engine_run", return_value=2.0):
            from app.evolution import _select_evolution_engine
            assert _select_evolution_engine() == "avo"

    def test_low_kept_ratio_returns_shinka(self):
        """kept_ratio < 0.20 → ShinkaEvolve (AVO too ambitious)."""
        # 1 kept out of 10 → 10% kept ratio
        recent = [{"status": "discard", "delta": -0.01} for _ in range(9)]
        recent.insert(0, {"status": "keep", "delta": 0.01})
        with patch.object(_FakeSettings, "evolution_engine", "auto", create=True), \
             patch("app.evolution._is_shinka_available", return_value=True), \
             patch("app.evolution._get_subia_safety_value", return_value=0.85), \
             patch("app.evolution.get_recent_results", return_value=recent), \
             patch("app.evolution_roi.days_since_engine_run", return_value=2.0):
            from app.evolution import _select_evolution_engine
            assert _select_evolution_engine() == "shinka"


class TestSelectorROIRecommendation:
    def test_roi_recommendation_picks_better_engine(self):
        """When both engines have data, ROI recommendation breaks the tie."""
        # Mid-range kept_ratio (e.g. 40%) so rules 6-7 don't fire
        mixed = (
            [{"status": "keep", "delta": 0.02} for _ in range(4)]
            + [{"status": "discard", "delta": -0.01} for _ in range(6)]
        )

        # Mock the ROI snapshot to show shinka with better cost-per-improvement
        from app.evolution_roi import ROISnapshot
        snapshot = ROISnapshot(
            window_days=14,
            total_cost_usd=2.00,
            real_improvements=4,
            rollbacks=0,
            rollback_rate=0.0,
            cost_per_improvement=0.50,
            sample_size=20,
            by_engine={
                "avo": {"experiments": 18, "cost_usd": 1.80, "real_improvements": 2, "cost_per_improvement": 0.90},
                "shinka": {"experiments": 2, "cost_usd": 0.20, "real_improvements": 2, "cost_per_improvement": 0.10},
                "meta": {"experiments": 0, "cost_usd": 0.0, "real_improvements": 0, "cost_per_improvement": None},
            },
        )

        with patch.object(_FakeSettings, "evolution_engine", "auto", create=True), \
             patch("app.evolution._is_shinka_available", return_value=True), \
             patch("app.evolution._get_subia_safety_value", return_value=0.85), \
             patch("app.evolution.get_recent_results", return_value=mixed), \
             patch("app.evolution_roi.days_since_engine_run", return_value=2.0), \
             patch("app.evolution_roi.get_rolling_roi", return_value=snapshot), \
             patch("app.evolution_roi.get_engine_recommendation", return_value="shinka"):
            from app.evolution import _select_evolution_engine
            # Shinka has better cost-per-improvement → ROI rec picks shinka
            assert _select_evolution_engine() == "shinka"

    def test_roi_skipped_when_no_shinka_data(self):
        """Without shinka data, ROI rule abstains and falls through to default."""
        mixed = (
            [{"status": "keep", "delta": 0.02} for _ in range(4)]
            + [{"status": "discard", "delta": -0.01} for _ in range(6)]
        )

        from app.evolution_roi import ROISnapshot
        # No shinka data
        snapshot = ROISnapshot(
            window_days=14, total_cost_usd=1.80, real_improvements=2,
            rollbacks=0, rollback_rate=0.0, cost_per_improvement=0.90,
            sample_size=10,
            by_engine={
                "avo": {"experiments": 10, "cost_usd": 1.80, "real_improvements": 2, "cost_per_improvement": 0.90},
                "shinka": {"experiments": 0, "cost_usd": 0.0, "real_improvements": 0, "cost_per_improvement": None},
                "meta": {"experiments": 0, "cost_usd": 0.0, "real_improvements": 0, "cost_per_improvement": None},
            },
        )

        with patch.object(_FakeSettings, "evolution_engine", "auto", create=True), \
             patch("app.evolution._is_shinka_available", return_value=True), \
             patch("app.evolution._get_subia_safety_value", return_value=0.85), \
             patch("app.evolution.get_recent_results", return_value=mixed), \
             patch("app.evolution_roi.days_since_engine_run", return_value=2.0), \
             patch("app.evolution_roi.get_rolling_roi", return_value=snapshot), \
             patch("app.evolution_roi.get_engine_recommendation", return_value="avo"):
            from app.evolution import _select_evolution_engine
            # Without shinka data, ROI rule abstains; default returns avo
            assert _select_evolution_engine() == "avo"
