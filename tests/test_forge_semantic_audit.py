"""Tests for app.forge.audit.semantic._call_judge.

Post-consolidation the judge routes through the factory
(``chat_completion_for_role`` → OpenRouter/Ollama). The former
Anthropic-direct + ``anthropic_credits`` breaker + OpenRouter-failover
dance (and ``_model_to_anthropic_id`` / ``_call_judge_openrouter`` /
``_call_judge_anthropic_direct``) was collapsed into a single factory
call. These tests pin the new contract: return the judge text on
success, ``None`` (fail-closed) on any failure or empty output.
"""
from __future__ import annotations

from tests._llm_fakes import patch_chat_completion


def test_judge_returns_text_on_success(monkeypatch):
    from app.forge.audit import semantic
    patch_chat_completion(monkeypatch, '{"verdict": "ok", "risk": 1}')
    assert semantic._call_judge("prompt", "claude-sonnet-4.6") == (
        '{"verdict": "ok", "risk": 1}'
    )


def test_judge_returns_none_on_provider_failure(monkeypatch):
    """Any exception from the factory → fail-closed None (the caller
    then produces a fail-closed reject, as before)."""
    from app.forge.audit import semantic
    patch_chat_completion(monkeypatch, raises=RuntimeError("provider down"))
    assert semantic._call_judge("prompt", "claude-sonnet-4.6") is None


def test_judge_returns_none_on_empty_output(monkeypatch):
    from app.forge.audit import semantic
    patch_chat_completion(monkeypatch, "")
    assert semantic._call_judge("prompt", "claude-sonnet-4.6") is None
