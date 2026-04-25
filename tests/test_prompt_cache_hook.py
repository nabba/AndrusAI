"""Tests for app/prompt_cache_hook.py — Anthropic cache_control injection."""
from unittest.mock import MagicMock

import pytest

from tests._v2_shim import install_settings_shim

install_settings_shim()

from app import prompt_cache_hook  # noqa: E402


class TestIsAnthropic:
    @pytest.mark.parametrize("model", [
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-haiku-3-5",
        "anthropic/claude-sonnet",
    ])
    def test_matches_anthropic_models(self, model):
        assert prompt_cache_hook._is_anthropic(model) is True

    @pytest.mark.parametrize("model", [
        "openrouter/deepseek/deepseek-chat",
        "ollama/qwen3.5:35b-a3b-q4_K_M",
        "gpt-4",
        "",
        None,
    ])
    def test_rejects_non_anthropic(self, model):
        assert prompt_cache_hook._is_anthropic(model) is False


class TestInjectCacheControl:
    def _long_system(self, chars=5000):
        return "You are Claude. " + ("lorem ipsum " * (chars // 12))[:chars]

    def test_long_system_becomes_block_form(self):
        sys_content = self._long_system()
        msgs = [{"role": "system", "content": sys_content},
                {"role": "user", "content": "hi"}]
        out = prompt_cache_hook._inject_cache_control(msgs)
        sys_out = out[0]
        assert sys_out["role"] == "system"
        assert isinstance(sys_out["content"], list)
        assert sys_out["content"][0]["type"] == "text"
        assert sys_out["content"][0]["cache_control"] == {"type": "ephemeral"}
        assert sys_out["content"][0]["text"] == sys_content

    def test_short_system_stays_string(self):
        msgs = [{"role": "system", "content": "brief"},
                {"role": "user", "content": "hi"}]
        out = prompt_cache_hook._inject_cache_control(msgs)
        assert out[0]["content"] == "brief"
        assert isinstance(out[0]["content"], str)

    def test_user_and_assistant_messages_untouched(self):
        sys_content = self._long_system()
        msgs = [
            {"role": "system", "content": sys_content},
            {"role": "user", "content": "user text"},
            {"role": "assistant", "content": "assistant text"},
        ]
        out = prompt_cache_hook._inject_cache_control(msgs)
        assert out[1]["content"] == "user text"
        assert out[2]["content"] == "assistant text"

    def test_empty_messages_safe(self):
        assert prompt_cache_hook._inject_cache_control([]) == []

    def test_only_first_system_injected(self):
        # Unusual but safe: if two system messages exist, only first is wrapped
        sys_content = self._long_system()
        msgs = [
            {"role": "system", "content": sys_content},
            {"role": "system", "content": sys_content},
            {"role": "user", "content": "hi"},
        ]
        out = prompt_cache_hook._inject_cache_control(msgs)
        assert isinstance(out[0]["content"], list)
        assert isinstance(out[1]["content"], str)

    def test_no_system_message_returned_unchanged(self):
        msgs = [{"role": "user", "content": "hi"}]
        out = prompt_cache_hook._inject_cache_control(msgs)
        assert out == msgs


class TestInstallCacheHook:
    def setup_method(self):
        prompt_cache_hook._installed = False

    def test_idempotent(self, monkeypatch):
        fake_litellm = MagicMock()
        original_completion = MagicMock()
        fake_litellm.completion = original_completion
        monkeypatch.setitem(__import__("sys").modules, "litellm", fake_litellm)

        prompt_cache_hook.install_cache_hook()
        first_completion = fake_litellm.completion
        prompt_cache_hook.install_cache_hook()  # second call
        assert prompt_cache_hook._installed is True
        assert fake_litellm.completion is first_completion

    def test_patched_completion_injects_on_anthropic(self, monkeypatch):
        fake_litellm = MagicMock()
        calls = []

        def original_completion(**kwargs):
            calls.append(kwargs)
            return "ok"

        fake_litellm.completion = original_completion
        # Remove async attr so we only test sync path
        if hasattr(fake_litellm, "acompletion"):
            delattr(fake_litellm, "acompletion")
        monkeypatch.setitem(__import__("sys").modules, "litellm", fake_litellm)

        prompt_cache_hook.install_cache_hook()

        long_sys = "x" * 5000
        fake_litellm.completion(
            model="claude-opus-4-6",
            messages=[
                {"role": "system", "content": long_sys},
                {"role": "user", "content": "hi"},
            ],
        )
        assert len(calls) == 1
        sys_msg = calls[0]["messages"][0]
        assert isinstance(sys_msg["content"], list)
        assert sys_msg["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_patched_completion_skips_non_anthropic(self, monkeypatch):
        fake_litellm = MagicMock()
        calls = []

        def original_completion(**kwargs):
            calls.append(kwargs)
            return "ok"

        fake_litellm.completion = original_completion
        if hasattr(fake_litellm, "acompletion"):
            delattr(fake_litellm, "acompletion")
        monkeypatch.setitem(__import__("sys").modules, "litellm", fake_litellm)

        prompt_cache_hook.install_cache_hook()

        long_sys = "x" * 5000
        fake_litellm.completion(
            model="openrouter/deepseek/deepseek-chat",
            messages=[
                {"role": "system", "content": long_sys},
                {"role": "user", "content": "hi"},
            ],
        )
        sys_msg = calls[0]["messages"][0]
        assert isinstance(sys_msg["content"], str)  # not rewritten

    def test_patched_completion_survives_injection_error(self, monkeypatch):
        fake_litellm = MagicMock()
        original_completion = MagicMock(return_value="ok")
        fake_litellm.completion = original_completion
        if hasattr(fake_litellm, "acompletion"):
            delattr(fake_litellm, "acompletion")
        monkeypatch.setitem(__import__("sys").modules, "litellm", fake_litellm)

        # Force injection to fail
        def boom(_msgs):
            raise RuntimeError("injection crashed")

        monkeypatch.setattr(prompt_cache_hook, "_inject_cache_control", boom)
        prompt_cache_hook.install_cache_hook()

        # Should still call the underlying completion (unchanged messages)
        fake_litellm.completion(model="claude-opus-4-6", messages=[{"role": "user", "content": "hi"}])
        assert original_completion.called
