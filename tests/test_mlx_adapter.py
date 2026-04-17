"""Tests for MLX adapter integration in llm_factory + training_pipeline."""
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests._v2_shim import install_settings_shim

install_settings_shim()


class TestGetPromotedAdapter:
    def test_returns_none_when_no_adapters(self, monkeypatch):
        from app import llm_factory
        monkeypatch.setattr("app.training_pipeline.list_adapters",
                            lambda: [])
        assert llm_factory._get_promoted_adapter("coder") is None

    def test_matches_role_specific_adapter(self, monkeypatch, tmp_path):
        from app import llm_factory
        from app.training_pipeline import AdapterInfo

        adapter_path = tmp_path / "my_adapter"
        adapter_path.mkdir()

        adapter = AdapterInfo(
            name="coder_adapter",
            adapter_path=str(adapter_path),
            promoted=True,
            agent_roles=["coder"],
        )
        monkeypatch.setattr("app.training_pipeline.list_adapters",
                            lambda: [adapter])
        out = llm_factory._get_promoted_adapter("coder")
        assert out == str(adapter_path)

    def test_matches_all_roles(self, monkeypatch, tmp_path):
        from app import llm_factory
        from app.training_pipeline import AdapterInfo

        adapter_path = tmp_path / "universal_adapter"
        adapter_path.mkdir()

        adapter = AdapterInfo(
            name="universal",
            adapter_path=str(adapter_path),
            promoted=True,
            agent_roles=["all"],
        )
        monkeypatch.setattr("app.training_pipeline.list_adapters",
                            lambda: [adapter])
        assert llm_factory._get_promoted_adapter("writer") == str(adapter_path)

    def test_skips_non_promoted(self, monkeypatch, tmp_path):
        from app import llm_factory
        from app.training_pipeline import AdapterInfo

        adapter_path = tmp_path / "pending"
        adapter_path.mkdir()
        adapter = AdapterInfo(name="pending", adapter_path=str(adapter_path),
                              promoted=False, agent_roles=["coder"])
        monkeypatch.setattr("app.training_pipeline.list_adapters",
                            lambda: [adapter])
        assert llm_factory._get_promoted_adapter("coder") is None

    def test_skips_adapter_missing_on_disk(self, monkeypatch):
        from app import llm_factory
        from app.training_pipeline import AdapterInfo

        adapter = AdapterInfo(name="ghost",
                              adapter_path="/tmp/does_not_exist_xyz",
                              promoted=True, agent_roles=["coder"])
        monkeypatch.setattr("app.training_pipeline.list_adapters",
                            lambda: [adapter])
        assert llm_factory._get_promoted_adapter("coder") is None


class TestAdapterLLM:
    def test_call_uses_bridge_mlx_generate(self, monkeypatch):
        from app.llm_factory import _AdapterLLM

        bridge = MagicMock()
        bridge.is_available.return_value = True
        bridge.mlx_generate.return_value = {"response": "adapter says hello"}

        monkeypatch.setattr("app.bridge_client.get_bridge",
                            lambda agent_id: bridge)
        llm = _AdapterLLM("qwen2.5-7b", "/path/to/adapter", max_tokens=512)
        out = llm.call("test prompt")
        assert out == "adapter says hello"
        bridge.mlx_generate.assert_called_once()

    def test_call_falls_back_to_ollama_when_bridge_unavailable(self, monkeypatch):
        from app import llm_factory

        # No bridge
        monkeypatch.setattr("app.bridge_client.get_bridge",
                            lambda agent_id: None)

        # Patch _get_LLM_class to return a fake LLM
        class FakeLLM:
            def __init__(self, **kw):
                self._k = kw
            def call(self, prompt):
                return "ollama response"
        monkeypatch.setattr(llm_factory, "_get_LLM_class",
                            lambda: FakeLLM)

        llm = llm_factory._AdapterLLM("qwen", "/path/adapter", max_tokens=256)
        out = llm.call("prompt")
        assert out == "ollama response"

    def test_call_falls_back_on_bridge_error(self, monkeypatch):
        from app import llm_factory

        bridge = MagicMock()
        bridge.is_available.return_value = True
        bridge.mlx_generate.return_value = {"error": "MLX crashed"}
        monkeypatch.setattr("app.bridge_client.get_bridge",
                            lambda agent_id: bridge)

        class FakeLLM:
            def __init__(self, **kw):
                pass
            def call(self, prompt):
                return "fallback text"
        monkeypatch.setattr(llm_factory, "_get_LLM_class", lambda: FakeLLM)

        llm = llm_factory._AdapterLLM("qwen", "/path/adapter")
        out = llm.call("p")
        assert out == "fallback text"

    def test_model_string_representation(self):
        from app.llm_factory import _AdapterLLM
        llm = _AdapterLLM("qwen2.5", "/adapter")
        assert llm.model == "mlx-adapter/qwen2.5"
        assert str(llm) == "mlx-adapter/qwen2.5"


class TestAdapterRegistryLoading:
    def test_orchestrator_init_loads_registry(self, tmp_path, monkeypatch):
        from app import training_pipeline

        # Redirect BOTH ADAPTERS_DIR (for registry.json + mkdir) and MODELS_DIR
        adapters_dir = tmp_path / "adapters"
        models_dir = tmp_path / "models"
        adapters_dir.mkdir()
        monkeypatch.setattr(training_pipeline, "ADAPTERS_DIR", adapters_dir)
        monkeypatch.setattr(training_pipeline, "MODELS_DIR", models_dir)

        # Seed registry.json
        (adapters_dir / "registry.json").write_text(json.dumps({
            "coder_adapter": {
                "name": "coder_adapter",
                "adapter_path": "/some/path",
                "promoted": True,
                "agent_roles": ["coder"],
                "eval_score": 0.85,
                "examples_count": 500,
            }
        }))

        # Clear and reset module globals
        training_pipeline._adapters.clear()
        training_pipeline._orchestrator = None
        training_pipeline.get_orchestrator()
        infos = training_pipeline.list_adapters()
        names = {i.name for i in infos}
        assert "coder_adapter" in names
        coder = next(i for i in infos if i.name == "coder_adapter")
        assert coder.promoted is True
        assert coder.agent_roles == ["coder"]
        assert coder.eval_score == 0.85

    def test_missing_registry_is_silent(self, tmp_path, monkeypatch):
        from app import training_pipeline
        adapters_dir = tmp_path / "adapters"
        models_dir = tmp_path / "models"
        adapters_dir.mkdir()
        monkeypatch.setattr(training_pipeline, "ADAPTERS_DIR", adapters_dir)
        monkeypatch.setattr(training_pipeline, "MODELS_DIR", models_dir)
        training_pipeline._adapters.clear()
        training_pipeline._orchestrator = None
        training_pipeline.get_orchestrator()
        assert training_pipeline.list_adapters() == []
