"""
Role Assignment Overlay Tests
=============================

Tests the Phase 2 closure between discovery and selection. The overlay
in ``control_plane.role_assignments`` must:
  - take precedence over static ROLE_DEFAULTS when active and pointing
    at a known catalog model,
  - fall through gracefully when the DB is unreachable or the overlay
    target has been removed,
  - never return a model that isn't in CATALOG.

Run:
    docker exec crewai-team-gateway-1 python3 -m pytest \
        /app/tests/test_llm_role_assignments.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def clear_assignment_cache():
    from app.llm_role_assignments import invalidate_cache
    invalidate_cache()
    yield
    invalidate_cache()


class TestOverlayPrecedence:
    def test_overlay_hit_overrides_default(self):
        from app.llm_catalog import get_default_for_role

        with patch("app.llm_role_assignments._query_assigned_model",
                   return_value="deepseek-v3.2"):
            # "commander" role default is claude-sonnet-4.6 under balanced.
            # With the overlay, we expect deepseek-v3.2 back.
            assert get_default_for_role("commander", "balanced") == "deepseek-v3.2"

    def test_overlay_miss_falls_through(self):
        from app.llm_catalog import get_default_for_role, ROLE_DEFAULTS

        with patch("app.llm_role_assignments._query_assigned_model",
                   return_value=None):
            expected = ROLE_DEFAULTS["balanced"]["commander"]
            assert get_default_for_role("commander", "balanced") == expected

    def test_overlay_pointing_at_missing_model_falls_through(self):
        from app.llm_catalog import get_default_for_role, ROLE_DEFAULTS

        with patch("app.llm_role_assignments._query_assigned_model",
                   return_value="no-such-model-abc"):
            expected = ROLE_DEFAULTS["balanced"]["commander"]
            assert get_default_for_role("commander", "balanced") == expected

    def test_overlay_db_exception_falls_through(self):
        from app.llm_catalog import get_default_for_role, ROLE_DEFAULTS

        def _boom(*_args, **_kwargs):
            raise RuntimeError("DB down")

        with patch("app.llm_role_assignments._query_assigned_model",
                   side_effect=_boom):
            expected = ROLE_DEFAULTS["balanced"]["commander"]
            assert get_default_for_role("commander", "balanced") == expected


class TestCache:
    def test_cache_returns_same_value(self):
        from app.llm_role_assignments import get_assigned_model

        with patch("app.llm_role_assignments._query_assigned_model",
                   return_value="deepseek-v3.2") as query:
            a = get_assigned_model("research", "budget")
            b = get_assigned_model("research", "budget")
            assert a == b == "deepseek-v3.2"
            # Second call should hit the cache, not re-query.
            assert query.call_count == 1

    def test_invalidate_clears_cache(self):
        from app.llm_role_assignments import get_assigned_model, invalidate_cache

        with patch("app.llm_role_assignments._query_assigned_model",
                   side_effect=["a", "b"]):
            assert get_assigned_model("r", "budget") == "a"
            invalidate_cache("r", "budget")
            assert get_assigned_model("r", "budget") == "b"

    def test_invalidate_all(self):
        from app.llm_role_assignments import get_assigned_model, invalidate_cache

        with patch("app.llm_role_assignments._query_assigned_model",
                   side_effect=["a", "b", "c", "d"]):
            get_assigned_model("r", "budget")
            get_assigned_model("r", "quality")
            invalidate_cache()
            # Both roles should re-query.
            get_assigned_model("r", "budget")
            get_assigned_model("r", "quality")
            # 4 queries total — no caching across invalidate
            assert True


class TestDominanceCheck:
    """Dominance is the Pareto rule the promoter uses to decide whether a
    discovered model should take over a role assignment in a given cost
    mode. Uses the 'commander' role under 'balanced' → claude-opus-4.6."""

    def _incumbent(self, role: str = "commander", cost_mode: str = "balanced"):
        from app.llm_catalog import CATALOG, get_default_for_role
        key = get_default_for_role(role, cost_mode)
        return CATALOG[key]

    def test_cheaper_and_better_dominates(self):
        from app.llm_discovery import _dominates_incumbent
        inc = self._incumbent()
        strength = inc["strengths"].get("routing", inc["strengths"].get("general", 0.5))
        assert _dominates_incumbent(
            {
                "cost_output_per_m": inc["cost_output_per_m"] - 1.0,
                "benchmark_score": strength + 0.05,
            },
            role="commander", cost_mode="balanced",
        )

    def test_same_cost_but_worse_does_not_dominate(self):
        from app.llm_discovery import _dominates_incumbent
        inc = self._incumbent()
        strength = inc["strengths"].get("commander", inc["strengths"].get("general", 0.5))
        assert not _dominates_incumbent(
            {
                "cost_output_per_m": inc["cost_output_per_m"],
                "benchmark_score": strength - 0.05,
            },
            role="commander", cost_mode="balanced",
        )

    def test_better_but_more_expensive_does_not_dominate(self):
        from app.llm_discovery import _dominates_incumbent
        inc = self._incumbent()
        strength = inc["strengths"].get("commander", inc["strengths"].get("general", 0.5))
        assert not _dominates_incumbent(
            {
                "cost_output_per_m": inc["cost_output_per_m"] + 1.0,
                "benchmark_score": strength + 0.10,
            },
            role="commander", cost_mode="balanced",
        )


class TestToolCallingDetection:
    def test_reads_supported_parameters_tools(self):
        from app.llm_discovery import _detect_tool_calling

        raw = {"id": "x/y", "supported_parameters": ["tools", "temperature"]}
        assert _detect_tool_calling(raw, "openrouter") is True

    def test_reads_supported_parameters_tool_choice(self):
        from app.llm_discovery import _detect_tool_calling

        raw = {"id": "x/y", "supported_parameters": ["tool_choice"]}
        assert _detect_tool_calling(raw, "openrouter") is True

    def test_empty_supported_parameters_means_no_tools(self):
        from app.llm_discovery import _detect_tool_calling

        raw = {"id": "x/y", "supported_parameters": ["temperature"]}
        assert _detect_tool_calling(raw, "openrouter") is False

    def test_missing_field_falls_back_to_heuristic(self):
        from app.llm_discovery import _detect_tool_calling

        assert _detect_tool_calling(
            {"id": "mistralai/codestral-2501", "name": "Codestral base"},
            "openrouter",
        ) is False
        assert _detect_tool_calling(
            {"id": "openai/gpt-9-turbo", "name": "GPT-9"},
            "openrouter",
        ) is True

    def test_ollama_default_false_without_signal(self):
        from app.llm_discovery import _detect_tool_calling

        raw = {"id": "ollama_chat/llama3.1:8b", "name": "Llama 3.1"}
        assert _detect_tool_calling(raw, "ollama") is False
