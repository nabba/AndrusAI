"""
Phase 4: live LLM predict_fn regression tests.

Verifies:
  - Stub LLM that returns clean JSON -> parsed into Prediction
  - JSON with ```json fences -> still parsed
  - Malformed response -> graceful fallback with low confidence
  - LLM that raises -> graceful fallback, no propagation
  - Prompt includes scene summary, homeostatic deviations, and recent
    accuracy
  - Homeostatic effect with non-numeric values is silently dropped
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.subia.kernel import (
    HomeostaticState,
    Prediction,
    SceneItem,
    SelfState,
)
from app.subia.prediction.llm_predict import (
    _build_prompt,
    _parse_json_block,
    build_llm_predict_fn,
)


# ── Prompt construction ─────────────────────────────────────────

class TestPromptConstruction:
    def test_prompt_includes_agent_role_and_task(self):
        ctx = {
            "agent_role": "researcher",
            "task_description": "ingest new Archibal data",
            "scene": [],
            "self_state": SelfState(),
            "homeostasis": HomeostaticState(),
            "prediction_history": [],
        }
        prompt = _build_prompt(ctx)
        assert "researcher" in prompt
        assert "ingest new Archibal data" in prompt
        assert "confidence" in prompt.lower()

    def test_prompt_renders_scene(self):
        ctx = {
            "agent_role": "r",
            "task_description": "x",
            "scene": [
                SceneItem(
                    id="s1", source="wiki", content_ref="c",
                    summary="Truepic Series C", salience=0.85,
                    entered_at="",
                )
            ],
            "self_state": SelfState(),
            "homeostasis": HomeostaticState(),
            "prediction_history": [],
        }
        prompt = _build_prompt(ctx)
        assert "Truepic Series C" in prompt
        assert "0.85" in prompt

    def test_prompt_shows_homeostatic_deviations(self):
        h = HomeostaticState(
            deviations={"coherence": 0.4, "progress": -0.35},
        )
        ctx = {
            "agent_role": "r", "task_description": "x",
            "scene": [], "self_state": SelfState(),
            "homeostasis": h, "prediction_history": [],
        }
        prompt = _build_prompt(ctx)
        assert "coherence" in prompt
        assert "progress" in prompt

    def test_prompt_calibrates_confidence_by_history(self):
        good = Prediction(
            id="p1", operation="o", predicted_outcome={},
            predicted_self_change={}, predicted_homeostatic_effect={},
            confidence=0.9, created_at="",
            resolved=True, prediction_error=0.1,
        )
        bad = Prediction(
            id="p2", operation="o", predicted_outcome={},
            predicted_self_change={}, predicted_homeostatic_effect={},
            confidence=0.9, created_at="",
            resolved=True, prediction_error=0.8,
        )
        ctx_good = {"agent_role": "r", "task_description": "x",
                    "scene": [], "self_state": SelfState(),
                    "homeostasis": HomeostaticState(),
                    "prediction_history": [good] * 5}
        ctx_bad = {"agent_role": "r", "task_description": "x",
                   "scene": [], "self_state": SelfState(),
                   "homeostasis": HomeostaticState(),
                   "prediction_history": [bad] * 5}
        p_good = _build_prompt(ctx_good)
        p_bad = _build_prompt(ctx_bad)
        assert "0.90" in p_good
        assert "0.20" in p_bad

    def test_empty_scene_labeled(self):
        ctx = {"agent_role": "r", "task_description": "x",
               "scene": [], "self_state": SelfState(),
               "homeostasis": HomeostaticState(),
               "prediction_history": []}
        prompt = _build_prompt(ctx)
        assert "(scene empty)" in prompt


# ── JSON parsing ────────────────────────────────────────────────

class TestJSONParsing:
    def test_clean_json_parses(self):
        text = '{"confidence": 0.7, "world_changes": {}}'
        parsed = _parse_json_block(text)
        assert parsed["confidence"] == 0.7

    def test_fenced_json_parses(self):
        text = '```json\n{"confidence": 0.7}\n```'
        parsed = _parse_json_block(text)
        assert parsed == {"confidence": 0.7}

    def test_bare_fence_parses(self):
        text = '```\n{"confidence": 0.8}\n```'
        parsed = _parse_json_block(text)
        assert parsed == {"confidence": 0.8}

    def test_prose_then_json_parses(self):
        text = 'Here is my answer:\n\n{"confidence": 0.6}'
        parsed = _parse_json_block(text)
        assert parsed == {"confidence": 0.6}

    def test_empty_text_returns_empty(self):
        assert _parse_json_block("") == {}
        assert _parse_json_block("no braces here") == {}

    def test_malformed_json_returns_empty(self):
        text = '{"confidence": 0.6,'  # trailing comma / truncation
        assert _parse_json_block(text) == {}


# ── Predict function: end-to-end with stub LLM ─────────────────

class TestEndToEnd:
    def _ctx(self, history=()):
        return {
            "agent_role": "researcher",
            "task_description": "ingest Archibal landscape update",
            "scene": [SceneItem(id="s1", source="wiki",
                                 content_ref="c", summary="x",
                                 salience=0.7, entered_at="")],
            "self_state": SelfState(),
            "homeostasis": HomeostaticState(),
            "prediction_history": list(history),
        }

    def test_stub_returns_json_gives_real_prediction(self):
        stub = MagicMock()
        stub.call = MagicMock(return_value=(
            '{"world_changes": {"wiki_pages_affected": ["a.md"],'
            ' "summary": "expected update"},'
            ' "self_changes": {"confidence_change": 0.1, "summary": "slight bump"},'
            ' "homeostatic_effects": {"coherence": 0.05},'
            ' "confidence": 0.75}'
        ))
        predict = build_llm_predict_fn(llm=stub)

        result = predict(self._ctx())
        assert isinstance(result, Prediction)
        assert result.confidence == 0.75
        assert result.predicted_outcome == {
            "wiki_pages_affected": ["a.md"], "summary": "expected update",
        }
        assert result.predicted_homeostatic_effect == {"coherence": 0.05}
        assert result.id.startswith("pred-")

    def test_stub_returns_fenced_json(self):
        stub = MagicMock()
        stub.call = MagicMock(return_value=(
            '```json\n'
            '{"confidence": 0.65, "world_changes": {}, '
            '"self_changes": {}, "homeostatic_effects": {}}\n'
            '```'
        ))
        predict = build_llm_predict_fn(llm=stub)
        result = predict(self._ctx())
        assert result.confidence == 0.65

    def test_stub_returns_garbage_gives_fallback(self):
        stub = MagicMock()
        stub.call = MagicMock(return_value="I don't follow instructions")
        predict = build_llm_predict_fn(llm=stub)
        result = predict(self._ctx())
        # Fallback: low confidence
        assert result.confidence == 0.3
        assert "(fallback" in result.predicted_outcome.get("summary", "")

    def test_stub_raises_gives_fallback(self):
        stub = MagicMock()
        stub.call = MagicMock(side_effect=RuntimeError("LLM down"))
        predict = build_llm_predict_fn(llm=stub)
        result = predict(self._ctx())
        assert result.confidence == 0.3

    def test_confidence_clamped_to_range(self):
        stub = MagicMock()
        stub.call = MagicMock(return_value='{"confidence": 5.0}')
        predict = build_llm_predict_fn(llm=stub)
        result = predict(self._ctx())
        assert 0.0 <= result.confidence <= 1.0

    def test_non_numeric_homeostatic_effect_dropped(self):
        stub = MagicMock()
        stub.call = MagicMock(return_value=(
            '{"confidence": 0.7,'
            ' "homeostatic_effects": {"coherence": "a lot", "progress": 0.1},'
            ' "world_changes": {}, "self_changes": {}}'
        ))
        predict = build_llm_predict_fn(llm=stub)
        result = predict(self._ctx())
        # Non-numeric "a lot" dropped; numeric progress preserved
        assert result.predicted_homeostatic_effect == {"progress": 0.1}


# ── Object-shape tolerance ─────────────────────────────────────

class TestObjectShapes:
    def test_llm_with_only_callable(self):
        def llm(prompt):
            return '{"confidence": 0.42, "world_changes": {},' \
                   ' "self_changes": {}, "homeostatic_effects": {}}'

        predict = build_llm_predict_fn(llm=llm)
        ctx = {
            "agent_role": "r", "task_description": "x",
            "scene": [], "self_state": SelfState(),
            "homeostasis": HomeostaticState(),
            "prediction_history": [],
        }
        result = predict(ctx)
        assert result.confidence == 0.42

    def test_llm_returns_dict_with_content(self):
        stub = MagicMock()
        stub.call = MagicMock(return_value={"content":
            '{"confidence": 0.55, "world_changes": {},' \
            ' "self_changes": {}, "homeostatic_effects": {}}'
        })
        predict = build_llm_predict_fn(llm=stub)
        ctx = {
            "agent_role": "r", "task_description": "x",
            "scene": [], "self_state": SelfState(),
            "homeostasis": HomeostaticState(),
            "prediction_history": [],
        }
        result = predict(ctx)
        assert result.confidence == 0.55

    def test_llm_with_no_call_method_fallback(self):
        class Broken:
            pass

        predict = build_llm_predict_fn(llm=Broken())
        ctx = {
            "agent_role": "r", "task_description": "x",
            "scene": [], "self_state": SelfState(),
            "homeostasis": HomeostaticState(),
            "prediction_history": [],
        }
        result = predict(ctx)
        assert result.confidence == 0.3   # fallback
