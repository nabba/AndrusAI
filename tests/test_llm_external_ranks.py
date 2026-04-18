"""
External LLM Ranks Tests
========================

Tests the Phase 3 external ranking integration. Covers:
  - catalog key resolution across prefix/suffix variants,
  - graceful degradation when a source is unreachable,
  - blended scoring in llm_benchmarks.get_scores.

Network calls are fully mocked.

Run:
    docker exec crewai-team-gateway-1 python3 -m pytest \
        /app/tests/test_llm_external_ranks.py -v
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestCatalogKeyResolution:
    def test_exact_catalog_key(self):
        from app.llm_external_ranks import _resolve_catalog_key
        assert _resolve_catalog_key("deepseek-v3.2") == "deepseek-v3.2"

    def test_openrouter_model_id(self):
        from app.llm_external_ranks import _resolve_catalog_key
        assert _resolve_catalog_key("openrouter/deepseek/deepseek-chat") == "deepseek-v3.2"

    def test_anthropic_model_id(self):
        from app.llm_external_ranks import _resolve_catalog_key
        assert _resolve_catalog_key("anthropic/claude-sonnet-4-6") == "claude-sonnet-4.6"

    def test_stripped_prefix(self):
        from app.llm_external_ranks import _resolve_catalog_key
        assert _resolve_catalog_key("deepseek/deepseek-chat") == "deepseek-v3.2"

    def test_unknown_returns_none(self):
        from app.llm_external_ranks import _resolve_catalog_key
        assert _resolve_catalog_key("some/totally-new-model") is None


class TestOpenRouterFetch:
    def test_emits_cost_and_speed_signals(self):
        from app import llm_external_ranks as ex

        models_payload = {
            "data": [
                {
                    "id": "deepseek/deepseek-chat",
                    "pricing": {"prompt": "0.00000028", "completion": "0.00000042"},
                },
                {
                    "id": "anthropic/claude-sonnet-4-6",
                    "pricing": {"prompt": "0.000001", "completion": "0.000005"},
                },
            ],
        }
        endpoints_payload = {
            "data": {"endpoints": [
                {"provider_name": "P1", "completion_tokens_per_second": 120},
            ]},
        }

        def _fake_get(url, headers=None, timeout=None):
            resp = MagicMock(status_code=200)
            resp.json.return_value = (
                endpoints_payload if "/endpoints" in url else models_payload
            )
            return resp

        settings = SimpleNamespace(
            openrouter_api_key=SimpleNamespace(get_secret_value=lambda: "fake")
        )
        with (patch("app.llm_external_ranks.httpx.get", side_effect=_fake_get),
              patch("app.config.get_settings", return_value=settings)):
            ranks = ex.fetch_openrouter_stats()

        by_model = {}
        for r in ranks:
            by_model.setdefault(r.model_id, []).append((r.metric, r.value))
        assert "deepseek-v3.2" in by_model
        metrics = dict(by_model["deepseek-v3.2"])
        assert "cost" in metrics
        assert pytest.approx(metrics["cost"], abs=0.01) == 0.42
        # speed_raw always emitted when endpoints respond
        assert "speed_raw" in metrics

    def test_missing_api_key_returns_empty(self):
        from app import llm_external_ranks as ex

        settings = SimpleNamespace(
            openrouter_api_key=SimpleNamespace(get_secret_value=lambda: "")
        )
        with (patch("app.config.get_settings", return_value=settings),
              patch.dict("os.environ", {"OPENROUTER_API_KEY": ""}, clear=False)):
            assert ex.fetch_openrouter_stats() == []


class TestHFLeaderboard:
    def test_missing_pandas_skips_gracefully(self):
        """Import error inside fetch_hf_leaderboard must never propagate."""
        import app.llm_external_ranks as ex

        with patch.dict("sys.modules", {"pandas": None, "pyarrow.parquet": None}):
            # Force the inner import to fail by shadowing with None.
            result = ex.fetch_hf_leaderboard()
            assert result == []


class TestArtificialAnalysis:
    def test_disabled_without_key(self):
        import app.llm_external_ranks as ex

        settings = SimpleNamespace(
            artificial_analysis_api_key=SimpleNamespace(get_secret_value=lambda: "")
        )
        with (patch("app.config.get_settings", return_value=settings),
              patch.dict("os.environ",
                         {"AA_API_KEY": "", "ARTIFICIAL_ANALYSIS_API_KEY": ""},
                         clear=False)):
            assert ex.fetch_artificial_analysis() == []

    def test_emits_quality_metric(self):
        """Matches the real AA v2 schema: evaluations nested, slug variants
        (adaptive, xhigh, …), median_output_tokens_per_second."""
        import app.llm_external_ranks as ex

        payload = {
            "data": [
                {
                    "slug": "deepseek-v3-2",
                    "evaluations": {
                        "artificial_analysis_intelligence_index": 75,
                    },
                    "median_output_tokens_per_second": 90,
                },
                # Two variants of Claude Sonnet 4.6 — the higher-intel one wins.
                {
                    "slug": "claude-sonnet-4-6-adaptive",
                    "evaluations": {
                        "artificial_analysis_intelligence_index": 51.7,
                    },
                    "median_output_tokens_per_second": 54.3,
                },
                {
                    "slug": "claude-sonnet-4-6-xhigh",
                    "evaluations": {
                        "artificial_analysis_intelligence_index": 78,
                    },
                    "median_output_tokens_per_second": 47.1,
                },
                # Legacy/fallback field placement still supported — but
                # the slug must resolve to a bootstrap catalog key. Sonnet
                # is the sole non-DeepSeek bootstrap entry.
                {
                    "slug": "claude-sonnet-4-6-non-reasoning-low-effort",
                    "intelligence_index": 40,
                    "median_output_tokens_per_second": 60,
                },
            ],
        }

        def _fake_get(url, headers=None, timeout=None):
            resp = MagicMock(status_code=200)
            resp.json.return_value = payload
            return resp

        settings = SimpleNamespace(
            artificial_analysis_api_key=SimpleNamespace(get_secret_value=lambda: "k")
        )
        with (patch("app.llm_external_ranks.httpx.get", side_effect=_fake_get),
              patch("app.config.get_settings", return_value=settings)):
            ranks = ex.fetch_artificial_analysis()

        by_model = {}
        for r in ranks:
            by_model.setdefault(r.model_id, {})[r.metric] = r.value
        assert by_model["deepseek-v3.2"]["quality"] == pytest.approx(0.75)
        # xhigh variant wins for claude-sonnet-4.6 (78 > 51.7 > 40)
        assert by_model["claude-sonnet-4.6"]["quality"] == pytest.approx(0.78)
        # Highest tok/s wins for speed_raw (60 > 54.3 > 47.1)
        assert by_model["claude-sonnet-4.6"]["speed_raw"] == pytest.approx(60.0)


class TestBlendedScoring:
    @pytest.fixture
    def isolated_benchmarks_db(self, monkeypatch, tmp_path):
        import app.llm_benchmarks as bm

        db_file = tmp_path / "benchmarks.db"
        monkeypatch.setattr(bm, "DB_PATH", db_file)
        bm._local = type(bm._local)()
        with bm._write_lock:
            bm._write_buffer.clear()
            bm._last_flush = 0.0
        yield bm

    def test_blend_respects_weight(self, isolated_benchmarks_db):
        import app.llm_benchmarks as bm

        # Build internal score of 0.5 for model-a.
        for _ in range(4):
            bm.record("model-a", "coding", True, latency_ms=60000, tokens=100)
        bm._flush_writes()
        internal = bm._internal_scores("coding")
        assert "model-a" in internal
        internal_a = internal["model-a"]

        with patch(
            "app.llm_external_ranks.get_external_score",
            side_effect=lambda name, tt: 0.9 if name == "model-a" else None,
        ):
            with patch("app.llm_catalog.CATALOG", {"model-a": {"strengths": {}}}):
                with patch(
                    "app.config.get_settings",
                    return_value=SimpleNamespace(
                        external_ranks_enabled=True,
                        external_ranks_weight=0.5,
                    ),
                ):
                    blended = bm.get_scores("coding")

        expected = 0.5 * internal_a + 0.5 * 0.9
        assert blended["model-a"] == pytest.approx(expected, abs=0.01)

    def test_blend_disabled_returns_internal(self, isolated_benchmarks_db):
        import app.llm_benchmarks as bm

        for _ in range(4):
            bm.record("model-a", "coding", True, latency_ms=60000, tokens=100)
        bm._flush_writes()
        internal = bm._internal_scores("coding")

        with patch(
            "app.config.get_settings",
            return_value=SimpleNamespace(
                external_ranks_enabled=False,
                external_ranks_weight=0.5,
            ),
        ):
            scores = bm.get_scores("coding")
        assert scores == internal

    def test_external_only_when_no_internal(self, isolated_benchmarks_db):
        import app.llm_benchmarks as bm

        with (patch(
                "app.llm_external_ranks.get_external_score",
                side_effect=lambda name, tt: 0.77 if name == "claude-sonnet-4.6" else None,
              ),
              patch("app.config.get_settings", return_value=SimpleNamespace(
                  external_ranks_enabled=True, external_ranks_weight=0.3,
              ))):
            scores = bm.get_scores("writing")
        # Internal has no rows; only catalog models with external signal
        # make it into the result.
        assert scores == {"claude-sonnet-4.6": pytest.approx(0.77)}
