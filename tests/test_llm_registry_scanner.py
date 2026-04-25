"""Tests for the Ollama registry scanner + host-capacity auto-detection.

Background — added 2026-04-25 to close the gap exposed by the qwen3.5
incident: ``llm_discovery.scan_ollama()`` only sees locally-pulled
models, so a strictly-better release like qwen3.5:35b-a3b-q4_K_M stayed
invisible for 3 weeks because nobody had pulled it yet.

The scanner crawls ollama.com/library/<family>/tags, filters by
quantization + size, and emits governance proposals. Size cap is
auto-detected from host RAM minus OS baseline minus Docker overhead so
the same code works on a 16 GB Mac, a 48 GB Mac, and a 256 GB workstation
without per-host constants.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app import llm_registry_scanner as scanner
from app.llm_registry_scanner import (
    HostCapacity,
    RegistryCandidate,
    diff_against_local,
    filter_candidates,
    parse_tags_page,
    probe_host_capacity,
    scan_ollama_registry,
)


# ══════════════════════════════════════════════════════════════════════
# Tags-page HTML parsing
# ══════════════════════════════════════════════════════════════════════

# Realistic HTML fragment matching what ollama.com renders. Captures the
# essential row structure: name, digest12, sizeGB, contextK, modality.
_QWEN35_HTML = """
<html>
<body>
<div class="tags">
qwen3.5:35b 3460ffeede54 • 24GB • 256K context window • Text, Image input • 1 month ago
qwen3.5:35b-a3b a1b2c3d4e5f6 • 22GB • 256K context window • Text, Image input • 3 weeks ago
qwen3.5:35b-a3b-q4_K_M b2c3d4e5f6a7 • 20GB • 256K context window • Text, Image input • 3 weeks ago
qwen3.5:35b-a3b-q8_0 c3d4e5f6a7b8 • 37GB • 256K context window • Text, Image input • 3 weeks ago
qwen3.5:35b-a3b-fp16 d4e5f6a7b8c9 • 70GB • 256K context window • Text, Image input • 3 weeks ago
qwen3.5:0.8b e5f6a7b8c9d0 • 0.5GB • 256K context window • Text input • 1 month ago
qwen3.5:122b f6a7b8c9d0e1 • 81GB • 256K context window • Text, Image input • 1 month ago
qwen3.5:122b-a10b 0a1b2c3d4e5f • 70GB • 256K context window • Text, Image input • 1 month ago
qwen3.5:9b 1a2b3c4d5e6f • 5.5GB • 256K context window • Text input • 1 month ago
</div>
</body>
</html>
"""


class TestParseTagsPage:

    def test_parses_realistic_listing(self):
        cands = parse_tags_page(_QWEN35_HTML, "qwen3.5")
        names = [c.full_name for c in cands]
        assert "qwen3.5:35b-a3b-q4_K_M" in names
        assert "qwen3.5:35b" in names
        assert "qwen3.5:0.8b" in names

    def test_parses_size_and_context(self):
        cands = parse_tags_page(_QWEN35_HTML, "qwen3.5")
        by_name = {c.full_name: c for c in cands}
        assert by_name["qwen3.5:35b-a3b-q4_K_M"].size_gb == 20.0
        assert by_name["qwen3.5:35b-a3b-q4_K_M"].context_k == 256

    def test_parses_modality(self):
        cands = parse_tags_page(_QWEN35_HTML, "qwen3.5")
        by_name = {c.full_name: c for c in cands}
        assert "Image" in by_name["qwen3.5:35b-a3b-q4_K_M"].modality
        assert "Image" not in by_name["qwen3.5:0.8b"].modality

    def test_detects_feature_hints(self):
        cands = parse_tags_page(_QWEN35_HTML, "qwen3.5")
        by_name = {c.full_name: c for c in cands}
        # MoE markers detected from -a3b suffix
        assert "moe-3b-active" in by_name["qwen3.5:35b-a3b-q4_K_M"].features
        # fp16 suffix flagged so the filter can drop it
        assert "full-precision" in by_name["qwen3.5:35b-a3b-fp16"].features

    def test_strict_family_match(self):
        """Parser ignores rows from a different family (paranoia — current
        Ollama pages don't mix families, but the regex must be strict)."""
        mixed = _QWEN35_HTML + "gemma3:27b 999 • 17GB • 128K context window • Text input • 2 months ago"
        cands = parse_tags_page(mixed, "qwen3.5")
        for c in cands:
            assert c.family == "qwen3.5"

    def test_dedupe_repeated_rows(self):
        """The Ollama page renders each tag twice (detailed + compact);
        parser must dedupe by full_name."""
        doubled = _QWEN35_HTML.replace(
            "qwen3.5:35b-a3b-q4_K_M",
            "qwen3.5:35b-a3b-q4_K_M",  # appears verbatim 2x in fixture
        )
        cands = parse_tags_page(doubled + _QWEN35_HTML, "qwen3.5")
        seen = [c.full_name for c in cands]
        assert len(seen) == len(set(seen))

    def test_empty_html_returns_empty(self):
        assert parse_tags_page("", "qwen3.5") == []


# ══════════════════════════════════════════════════════════════════════
# Filtering — size cap + quant preference
# ══════════════════════════════════════════════════════════════════════

class TestFilterCandidates:

    def _mk(self, tag: str, size_gb: float) -> RegistryCandidate:
        return RegistryCandidate(
            family="qwen3.5", tag=tag,
            full_name=f"qwen3.5:{tag}", digest="abc",
            size_gb=size_gb, context_k=256, modality="Text",
            features=[],
        )

    def test_drops_oversized(self):
        cands = [self._mk("35b-a3b-q4_K_M", 20.0),
                 self._mk("35b-a3b-q8_0", 37.0)]
        kept = filter_candidates(cands, max_size_gb=24.0)
        names = [c.full_name for c in kept]
        assert "qwen3.5:35b-a3b-q4_K_M" in names
        assert "qwen3.5:35b-a3b-q8_0" not in names

    def test_drops_undersized(self):
        cands = [self._mk("0.8b", 0.5), self._mk("9b", 5.5)]
        kept = filter_candidates(cands, max_size_gb=24.0, min_size_gb=4.0)
        assert {c.full_name for c in kept} == {"qwen3.5:9b"}

    def test_drops_exotic_quants(self):
        cands = [self._mk("35b-a3b-q4_K_M", 20.0),
                 self._mk("35b-a3b-fp16", 22.0),    # over-precise — drop
                 self._mk("35b-a3b-bf16", 22.0),    # over-precise — drop
                 self._mk("35b-a3b-mxfp8", 22.0)]   # experimental — drop
        kept = filter_candidates(cands, max_size_gb=24.0)
        assert [c.tag for c in kept] == ["35b-a3b-q4_K_M"]

    def test_sort_prefers_features_then_smaller(self):
        cands = [
            RegistryCandidate(family="qwen3.5", tag="35b", full_name="qwen3.5:35b",
                              digest="x", size_gb=24.0, context_k=256,
                              modality="Text", features=[]),
            RegistryCandidate(family="qwen3.5", tag="9b", full_name="qwen3.5:9b",
                              digest="y", size_gb=5.5, context_k=256,
                              modality="Text", features=[]),
            RegistryCandidate(family="qwen3.5", tag="35b-a3b-q4_K_M",
                              full_name="qwen3.5:35b-a3b-q4_K_M",
                              digest="z", size_gb=20.0, context_k=256,
                              modality="Text",
                              features=["moe-3b-active"]),
        ]
        kept = filter_candidates(cands, max_size_gb=24.0)
        # MoE feature wins; among non-feature, smaller wins
        assert kept[0].tag == "35b-a3b-q4_K_M"


# ══════════════════════════════════════════════════════════════════════
# Local-tag dedupe
# ══════════════════════════════════════════════════════════════════════

class TestDiffAgainstLocal:

    def test_drops_already_pulled(self):
        cands = [
            RegistryCandidate(family="qwen3.5", tag="35b", full_name="qwen3.5:35b",
                              digest="x", size_gb=24, context_k=256,
                              modality="Text", features=[]),
            RegistryCandidate(family="qwen3.5", tag="35b-a3b-q4_K_M",
                              full_name="qwen3.5:35b-a3b-q4_K_M",
                              digest="y", size_gb=20, context_k=256,
                              modality="Text", features=[]),
        ]
        local = ["qwen3.5:35b", "llama3.1:8b"]
        new = diff_against_local(cands, local)
        assert {c.full_name for c in new} == {"qwen3.5:35b-a3b-q4_K_M"}


# ══════════════════════════════════════════════════════════════════════
# Host-capacity auto-detection — the headline feature
# ══════════════════════════════════════════════════════════════════════

class TestProbeHostCapacity:
    """Auto-detected size cap replaces the hardcoded 24 GB constant.

    The deepseek-r1:32b SIGKILL spiral happened because two ~38 GB models
    loaded into 48 GB unified memory simultaneously. A static cap
    couldn't have prevented that on a smaller machine; a static cap on
    a bigger machine wastes capability. Auto-detection adapts.
    """

    def test_returns_none_when_ram_undetectable(self, monkeypatch):
        monkeypatch.setattr(scanner, "_detect_total_ram_gb", lambda: 0.0)
        assert probe_host_capacity() is None

    def test_48gb_mac_with_1_loaded_model(self, monkeypatch):
        """Reproduce the actual host this code was built on:
        48 GB Mac, OLLAMA_MAX_LOADED_MODELS=1, ~14 GB Docker overhead.

        Uses HOST_TOTAL_RAM_GB so the probe takes the host-authoritative
        path (and DOES count container limits as overhead — see the
        Docker-on-Mac caveat in probe_host_capacity docstring)."""
        monkeypatch.setenv("HOST_TOTAL_RAM_GB", "48")
        monkeypatch.setattr(scanner, "_detect_os_baseline_gb", lambda: 10.0)
        monkeypatch.setattr(scanner, "_detect_docker_overhead_gb", lambda: 14.0)
        monkeypatch.setattr(scanner, "_detect_max_loaded_models", lambda: 1)
        cap = probe_host_capacity()
        assert cap is not None
        assert cap.total_ram_gb == 48.0
        assert cap.source == "env"
        assert cap.ollama_budget_gb == 24.0  # 48 - 10 - 14
        # 24 / 1 / 1.20 * 0.95 = 19.0 GB — fits qwen3.5:35b-a3b-q4_K_M (20 GB)
        # at marginal verdict, drops the q8_0 (37 GB) decisively.
        assert 18.5 <= cap.max_model_size_gb <= 19.5

    def test_16gb_mac_caps_aggressively(self, monkeypatch):
        """Verify a 16 GB Mac would NOT have proposed 20 GB models."""
        monkeypatch.setenv("HOST_TOTAL_RAM_GB", "16")
        monkeypatch.setattr(scanner, "_detect_os_baseline_gb", lambda: 8.0)
        monkeypatch.setattr(scanner, "_detect_docker_overhead_gb", lambda: 0.0)
        monkeypatch.setattr(scanner, "_detect_max_loaded_models", lambda: 1)
        cap = probe_host_capacity()
        assert cap is not None
        # 16 - 8 = 8 GB budget; 8 / 1 / 1.20 * 0.95 ≈ 6.3 GB
        assert cap.max_model_size_gb < 7.0
        # qwen3.5:35b-a3b-q4_K_M (20 GB) would be filtered out
        assert cap.max_model_size_gb < 20.0

    def test_max_loaded_models_2_halves_per_model_budget(self, monkeypatch):
        """OLLAMA_MAX_LOADED_MODELS=2 means each model gets half the budget.
        This is what we backed away from after the SIGKILL spiral."""
        monkeypatch.setenv("HOST_TOTAL_RAM_GB", "48")
        monkeypatch.setattr(scanner, "_detect_os_baseline_gb", lambda: 10.0)
        monkeypatch.setattr(scanner, "_detect_docker_overhead_gb", lambda: 14.0)
        monkeypatch.setattr(scanner, "_detect_max_loaded_models", lambda: 2)
        cap = probe_host_capacity()
        assert cap is not None
        # 24 / 2 / 1.20 * 0.95 ≈ 9.5 GB
        assert 9.0 <= cap.max_model_size_gb <= 10.0

    def test_docker_view_does_not_double_count(self, monkeypatch):
        """When total_ram comes from /proc/meminfo (Docker container view),
        the scanner must NOT also subtract container limits — that's the
        same memory measured twice. Bug fix from 2026-04-25 testing."""
        monkeypatch.delenv("HOST_TOTAL_RAM_GB", raising=False)
        # Simulate Docker-on-Mac: /proc/meminfo shows VM size 23.4 GB
        def _patched_total():
            scanner._LAST_PROBE_SOURCE["source"] = "proc_meminfo"
            return 23.4
        monkeypatch.setattr(scanner, "_detect_total_ram_gb", _patched_total)
        monkeypatch.setattr(scanner, "_detect_os_baseline_gb", lambda: 4.0)
        # Even if container limits sum to 22.8, we MUST NOT subtract again
        monkeypatch.setattr(scanner, "_detect_docker_overhead_gb", lambda: 22.8)
        monkeypatch.setattr(scanner, "_detect_max_loaded_models", lambda: 1)
        cap = probe_host_capacity()
        assert cap is not None
        # Should be 23.4 - 4.0 - 0 = 19.4 (overhead skipped)
        # NOT 23.4 - 4.0 - 22.8 = -3.4 (the regressed math)
        assert cap.docker_overhead_gb == 0.0
        assert cap.ollama_budget_gb == 19.4
        assert cap.max_model_size_gb > 0  # the regressed math gave 0.0


class TestSizeCapResolution:
    """Resolution priority: env override > auto-detect > fallback."""

    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("LLM_REGISTRY_MAX_SIZE_GB", "8.5")
        # Even if auto-detect would say 19, env wins.
        monkeypatch.setattr(
            scanner, "probe_host_capacity",
            lambda: HostCapacity(48, 10, 14, 24, 1, 1.2, 19.0, "test"),
        )
        assert scanner._max_size_from_env() == 8.5

    def test_auto_detect_used_when_no_env(self, monkeypatch):
        monkeypatch.delenv("LLM_REGISTRY_MAX_SIZE_GB", raising=False)
        monkeypatch.setattr(
            scanner, "probe_host_capacity",
            lambda: HostCapacity(48, 10, 14, 24, 1, 1.2, 19.0, "test"),
        )
        assert scanner._max_size_from_env() == 19.0

    def test_fallback_when_probe_fails(self, monkeypatch):
        monkeypatch.delenv("LLM_REGISTRY_MAX_SIZE_GB", raising=False)
        monkeypatch.setattr(scanner, "probe_host_capacity", lambda: None)
        assert scanner._max_size_from_env() == scanner._DEFAULT_MAX_SIZE_GB_FALLBACK

    def test_invalid_env_falls_through_to_auto(self, monkeypatch):
        monkeypatch.setenv("LLM_REGISTRY_MAX_SIZE_GB", "not-a-number")
        monkeypatch.setattr(
            scanner, "probe_host_capacity",
            lambda: HostCapacity(48, 10, 14, 24, 1, 1.2, 19.0, "test"),
        )
        assert scanner._max_size_from_env() == 19.0


# ══════════════════════════════════════════════════════════════════════
# End-to-end — scan_ollama_registry()
# ══════════════════════════════════════════════════════════════════════

class TestScanOllamaRegistry:

    def test_disabled_returns_empty(self, monkeypatch):
        monkeypatch.setenv("LLM_REGISTRY_SCAN_ENABLED", "false")
        out = scan_ollama_registry(fetch=lambda fam: _QWEN35_HTML)
        assert out == []

    def test_filters_by_capacity(self, monkeypatch):
        monkeypatch.setenv("LLM_REGISTRY_SCAN_ENABLED", "true")
        # Force capacity = 19 GB → q4_K_M (20 GB) just barely doesn't fit
        out = scan_ollama_registry(
            families=("qwen3.5",),
            max_size_gb=19.0,
            fetch=lambda fam: _QWEN35_HTML,
        )
        names = [c.full_name for c in out]
        # 20 GB > 19 GB cap → dropped
        assert "qwen3.5:35b-a3b-q4_K_M" not in names
        # 5.5 GB still passes if min_size_gb default (4) is satisfied
        assert "qwen3.5:9b" in names

    def test_caps_to_oversized_at_24gb(self, monkeypatch):
        monkeypatch.setenv("LLM_REGISTRY_SCAN_ENABLED", "true")
        out = scan_ollama_registry(
            families=("qwen3.5",),
            max_size_gb=24.0,
            fetch=lambda fam: _QWEN35_HTML,
        )
        names = [c.full_name for c in out]
        # The MoE q4_K_M now fits
        assert "qwen3.5:35b-a3b-q4_K_M" in names
        # The q8_0 (37 GB) and 122b (81 GB) variants always too big
        assert "qwen3.5:35b-a3b-q8_0" not in names
        assert "qwen3.5:122b" not in names
