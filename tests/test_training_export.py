"""Tests for training_collector ShareGPT/Alpaca/MLX export."""
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests._v2_shim import install_settings_shim

install_settings_shim()


@pytest.fixture(autouse=True)
def _isolated_curated_dir(tmp_path, monkeypatch):
    """Point CurationPipeline paths inside tmp_path so it never touches /app."""
    import app.training_collector as tc
    monkeypatch.setattr(tc, "CURATED_DIR", tmp_path / "curated")
    monkeypatch.setattr(tc, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(tc, "TRAINING_DATA_DIR", tmp_path)
    yield


class TestConverters:
    def _record(self):
        return {
            "id": "r1",
            "agent_role": "coder",
            "task_description": "Write a factorial function",
            "messages": [
                {"role": "system", "content": "You are a coding assistant."},
                {"role": "user", "content": "write factorial"},
            ],
            "response": "def factorial(n): return 1 if n <= 1 else n * factorial(n - 1)",
            "quality_score": 0.8,
        }

    def test_to_sharegpt(self):
        from app.training_collector import CurationPipeline
        p = CurationPipeline()
        out = p._to_sharegpt(self._record())
        convs = out["conversations"]
        # system + user + final gpt
        assert convs[0]["from"] == "system"
        assert convs[1]["from"] == "human"
        assert convs[-1]["from"] == "gpt"
        assert "factorial" in convs[-1]["value"]

    def test_to_alpaca(self):
        from app.training_collector import CurationPipeline
        p = CurationPipeline()
        out = p._to_alpaca(self._record())
        assert out["instruction"] == "write factorial"
        assert out["input"].startswith("You are a coding")
        assert "factorial" in out["output"]

    def test_to_alpaca_no_user_message(self):
        from app.training_collector import CurationPipeline
        p = CurationPipeline()
        r = self._record()
        r["messages"] = [{"role": "system", "content": "sys"}]
        out = p._to_alpaca(r)
        assert out["instruction"] == ""

    def test_to_mlx_format(self):
        from app.training_collector import CurationPipeline
        p = CurationPipeline()
        out = p._to_mlx_format(self._record())
        msgs = out["messages"]
        # system + user + assistant (response)
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert msgs[-1]["role"] == "assistant"
        assert "factorial" in msgs[-1]["content"]


class TestExportFormat:
    def _patch_eligible(self, monkeypatch, records):
        from app.training_collector import CurationPipeline
        monkeypatch.setattr(CurationPipeline, "_fetch_eligible",
                            lambda self: records)

    def _record(self, rid="r1"):
        return {
            "id": rid, "agent_role": "writer",
            "task_description": "summarize",
            "messages": [{"role": "user", "content": "do it"}],
            "response": "here you go",
            "quality_score": 0.9,
        }

    def test_no_eligible_returns_error(self, monkeypatch):
        self._patch_eligible(monkeypatch, [])
        from app.training_collector import CurationPipeline
        p = CurationPipeline()
        out = p.export_format("sharegpt")
        assert out["exported"] == 0
        assert "No eligible data" in out["error"]

    def test_unknown_format_returns_error(self, monkeypatch):
        self._patch_eligible(monkeypatch, [self._record()])
        from app.training_collector import CurationPipeline
        p = CurationPipeline()
        out = p.export_format("xml")
        assert out["exported"] == 0
        assert "Unknown format" in out["error"]

    def test_exports_sharegpt_to_json_file(self, monkeypatch, tmp_path):
        self._patch_eligible(monkeypatch, [self._record("a"), self._record("b")])
        from app.training_collector import CurationPipeline
        p = CurationPipeline()
        out_path = tmp_path / "sg.json"
        result = p.export_format("sharegpt", output_path=str(out_path))
        assert result["exported"] == 2
        assert out_path.exists()
        data = json.loads(out_path.read_text())
        assert isinstance(data, list)
        assert len(data) == 2
        assert "conversations" in data[0]

    def test_exports_alpaca_to_json_file(self, monkeypatch, tmp_path):
        self._patch_eligible(monkeypatch, [self._record()])
        from app.training_collector import CurationPipeline
        out_path = tmp_path / "al.json"
        result = CurationPipeline().export_format("alpaca", output_path=str(out_path))
        assert result["exported"] == 1
        data = json.loads(out_path.read_text())
        assert "instruction" in data[0]
        assert "output" in data[0]

    def test_exports_mlx_to_jsonl(self, monkeypatch, tmp_path):
        self._patch_eligible(monkeypatch, [self._record("a"), self._record("b")])
        from app.training_collector import CurationPipeline
        out_path = tmp_path / "mlx.jsonl"
        result = CurationPipeline().export_format("mlx", output_path=str(out_path))
        assert result["exported"] == 2
        # One record per line in jsonl
        lines = [l for l in out_path.read_text().splitlines() if l.strip()]
        assert len(lines) == 2
        for line in lines:
            rec = json.loads(line)
            assert "messages" in rec
