"""Tests for the evolver container entrypoint's pure logic
(app/self_improvement/evolver_job.py): the anchored editor, the edit parser,
and the gateway-side result extraction. The LLM/Docker parts run only in the
container and are not exercised here.
"""
from __future__ import annotations

import json

import pytest

try:
    from app.self_improvement.evolver_job import (
        apply_anchored_edits,
        extract_result,
        make_anchored_editor,
        parse_edits,
        _RESULT_BEGIN,
        _RESULT_END,
    )
    from app.self_improvement.change_spec import ChangeSpec
except Exception as exc:  # pragma: no cover
    pytest.skip(f"app import unavailable: {exc}", allow_module_level=True)


# ── apply_anchored_edits ─────────────────────────────────────────────────────


def test_apply_single_edit():
    src = "def f():\n    return 1\n"
    out = apply_anchored_edits(src, [{"find": "return 1", "replace": "return 2"}])
    assert out == "def f():\n    return 2\n"


def test_apply_multiple_edits_in_order():
    src = "a = 1\nb = 2\n"
    out = apply_anchored_edits(
        src, [{"find": "a = 1", "replace": "a = 10"}, {"find": "b = 2", "replace": "b = 20"}]
    )
    assert out == "a = 10\nb = 20\n"


def test_missing_anchor_raises():
    with pytest.raises(ValueError, match="not found"):
        apply_anchored_edits("x = 1\n", [{"find": "y = 2", "replace": "y = 3"}])


def test_ambiguous_anchor_raises():
    # "x" appears twice → not unique → refuse rather than guess.
    with pytest.raises(ValueError, match="not unique"):
        apply_anchored_edits("x\nx\n", [{"find": "x", "replace": "z"}])


def test_empty_find_raises():
    with pytest.raises(ValueError, match="empty find"):
        apply_anchored_edits("x = 1\n", [{"find": "", "replace": "y"}])


# ── parse_edits ──────────────────────────────────────────────────────────────


def test_parse_plain_json_array():
    assert parse_edits('[{"find": "a", "replace": "b"}]') == [{"find": "a", "replace": "b"}]


def test_parse_fenced_json():
    raw = "Here are the edits:\n```json\n[{\"find\": \"a\", \"replace\": \"b\"}]\n```\nDone."
    assert parse_edits(raw) == [{"find": "a", "replace": "b"}]


def test_parse_prose_wrapped_array():
    raw = 'I suggest: [{"find": "a", "replace": "b"}] — that should do it.'
    assert parse_edits(raw) == [{"find": "a", "replace": "b"}]


def test_parse_non_array_raises():
    with pytest.raises((ValueError, json.JSONDecodeError)):
        parse_edits('{"find": "a"}')


# ── make_anchored_editor ─────────────────────────────────────────────────────


def _spec(src: str) -> ChangeSpec:
    return ChangeSpec(
        target_file="app/x.py",
        module_path="app.x",
        full_source=src,
        preservation_assertions=["import app.x as _m"],
    )


def test_editor_applies_llm_edits():
    spec = _spec("def f():\n    return 1\n")
    editor = make_anchored_editor(
        lambda prompt: '[{"find": "return 1", "replace": "return 42"}]'
    )
    assert editor(spec, "make f return 42") == "def f():\n    return 42\n"


def test_editor_refuses_too_many_edits():
    spec = _spec("a=1\n")
    many = json.dumps([{"find": "a=1", "replace": "a=2"}] * 50)
    editor = make_anchored_editor(lambda prompt: many, max_edits=12)
    with pytest.raises(ValueError, match="too many edits"):
        editor(spec, "x")


def test_editor_propagates_bad_anchor():
    spec = _spec("a=1\n")
    editor = make_anchored_editor(lambda prompt: '[{"find": "zzz", "replace": "q"}]')
    with pytest.raises(ValueError, match="not found"):
        editor(spec, "x")


# ── extract_result (gateway side) ────────────────────────────────────────────


def test_extract_result_from_noisy_logs():
    payload = {"ok": True, "result": {"verdict": {"verdict": "IMPROVED"}}}
    logs = (
        "INFO some startup noise\n"
        "pytest output blah\n"
        + _RESULT_BEGIN + json.dumps(payload) + _RESULT_END + "\n"
        "trailing noise\n"
    )
    assert extract_result(logs) == payload


def test_extract_result_missing_sentinel_raises():
    with pytest.raises(ValueError, match="no evolver result sentinel"):
        extract_result("just logs, no result\n")
