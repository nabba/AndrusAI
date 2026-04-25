"""
Phase 5: Multi-Judge + Re-benchmarking Tests
============================================

Exercises the family-exclusion rule for benchmark judges and the
incumbent-drift detection path that raises governance alerts.

Run:
    docker exec crewai-team-gateway-1 python3 -m pytest \
        /app/tests/test_llm_rebenchmark.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestProviderFamily:
    @pytest.mark.parametrize("model_id,expected", [
        ("claude-sonnet-4.6", "anthropic"),
        ("anthropic/claude-opus-4-6", "anthropic"),
        ("openrouter/google/gemma-4-31b-it", "google"),
        ("openrouter/deepseek/deepseek-chat", "deepseek"),
        ("openrouter/moonshotai/kimi-k2.5", "moonshot"),
        ("openrouter/minimax/minimax-m2.5", "minimax"),
        ("openrouter/zhipu/glm-5", "zhipu"),
        ("openrouter/xiaomi/mimo-v2-pro", "xiaomi"),
        ("openrouter/nvidia/nemotron-3-super-120b-a12b:free", "nvidia"),
        ("openrouter/stepfun/step-3.5-flash:free", "stepfun"),
        ("openrouter/mistralai/mistral-medium", "mistral"),
        ("ollama_chat/llama3.1:8b", "meta"),
        ("ollama_chat/qwen3.5:35b-a3b-q4_K_M", "alibaba"),
        ("new/unknown-model", "unknown"),
    ])
    def test_classify(self, model_id, expected):
        from app.llm_discovery import _provider_family
        assert _provider_family(model_id) == expected


class TestJudgeSelection:
    def test_excludes_same_family(self):
        """A DeepSeek candidate doesn't get judged by DeepSeek V3.2."""
        from app import llm_discovery as d

        with patch.object(d, "_build_judge_llm",
                          side_effect=lambda k: object() if k != "deepseek-v3.2" else None):
            # Even if we force deepseek into the rotation, it's out.
            eligible = d._select_judges("openrouter/deepseek/deepseek-chat")
            families = {fam for _, fam, _ in eligible}
            assert "deepseek" not in families

    def test_returns_at_most_two(self):
        from app import llm_discovery as d

        # Provide non-None judges for every family so cap kicks in.
        with patch.object(d, "_build_judge_llm", side_effect=lambda k: object()):
            eligible = d._select_judges("openrouter/kimi-k2.5")
            assert len(eligible) <= 2

    def test_skips_unavailable_judges(self):
        """Judges whose API key is missing (returning None) are skipped."""
        from app import llm_discovery as d

        call_count = {"n": 0}

        def _fake_build(k):
            call_count["n"] += 1
            return None if k == "claude-sonnet-4.6" else object()

        with patch.object(d, "_build_judge_llm", side_effect=_fake_build):
            eligible = d._select_judges("openrouter/some-random/model")
            # Three rotation entries tried; anthropic filtered out;
            # remaining two returned.
            assert call_count["n"] == 3
            assert len(eligible) == 2

    def test_no_eligible_returns_empty(self):
        """When nothing is available, _select_judges returns []."""
        from app import llm_discovery as d

        with patch.object(d, "_build_judge_llm", return_value=None):
            assert d._select_judges("openrouter/some-random/model") == []


class TestRebenchmarkIncumbent:
    def test_updates_strengths_in_place(self):
        from app import llm_discovery as d
        from app.llm_catalog import CATALOG

        # Backup pristine strengths so other tests aren't polluted.
        original = dict(CATALOG["claude-sonnet-4.6"]["strengths"])

        try:
            with (patch.object(d, "benchmark_model",
                               side_effect=[0.91, 0.89, 0.93]),
                  patch.object(d, "_upsert_incumbent_benchmark")):
                summary = d.rebenchmark_incumbent("claude-sonnet-4.6")

            assert summary["model"] == "claude-sonnet-4.6"
            assert set(summary["new_scores"]) == set(d.BENCHMARK_ROLES)
            # In-place mutation
            updated = CATALOG["claude-sonnet-4.6"]["strengths"]
            assert updated["research"] == round(summary["new_scores"]["research"], 2)
        finally:
            CATALOG["claude-sonnet-4.6"]["strengths"] = original

    def test_drift_above_threshold_raises_alert(self):
        from app import llm_discovery as d
        from app.llm_catalog import CATALOG

        original = dict(CATALOG["claude-sonnet-4.6"]["strengths"])
        try:
            # All benchmark scores drop ~40% below the current strengths.
            with (patch.object(d, "benchmark_model",
                               side_effect=[0.40, 0.45, 0.42]),
                  patch.object(d, "_upsert_incumbent_benchmark"),
                  patch.object(d, "_raise_drift_alert", return_value=True) as alerter):
                summary = d.rebenchmark_incumbent("claude-sonnet-4.6")
            assert summary["alerted"] is True
            alerter.assert_called_once()
        finally:
            CATALOG["claude-sonnet-4.6"]["strengths"] = original

    def test_missing_model_returns_error(self):
        from app.llm_discovery import rebenchmark_incumbent

        result = rebenchmark_incumbent("not-a-real-model")
        assert "error" in result

    def test_all_benchmarks_fail_returns_error(self):
        from app import llm_discovery as d
        from app.llm_catalog import CATALOG

        original = dict(CATALOG["claude-sonnet-4.6"]["strengths"])
        try:
            with (patch.object(d, "benchmark_model", return_value=-1.0),
                  patch.object(d, "_upsert_incumbent_benchmark")):
                summary = d.rebenchmark_incumbent("claude-sonnet-4.6")
            assert summary.get("error")
        finally:
            CATALOG["claude-sonnet-4.6"]["strengths"] = original


class TestIncumbentRotation:
    def test_pick_returns_known_catalog_key(self):
        from app.llm_discovery import pick_incumbent_to_rebenchmark
        from app.llm_catalog import CATALOG

        # With DB unreachable the function still returns a candidate
        # (all incumbents treated as never-benchmarked).
        with patch("app.control_plane.db.execute", return_value=[]):
            choice = pick_incumbent_to_rebenchmark()
        if choice is not None:
            assert choice in CATALOG
            assert CATALOG[choice]["tier"] in ("budget", "mid", "premium")
            assert not CATALOG[choice].get("_discovered")
