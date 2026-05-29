"""Tests for app/llm_cache_control.py — prompt-cache cache_control injection.

Replaces tests/test_prompt_cache_hook.py. The injection moved out of a
global litellm monkeypatch into a pure helper called by the factory's own
LLM paths (BudgetAwareCompletion.call + ChatCompletionHandle.create) in the
OpenRouter+Ollama consolidation.
"""
import pytest

from app.llm_cache_control import _supports_cache_control, inject_cache_control


class TestSupportsCacheControl:
    @pytest.mark.parametrize("model", [
        "openrouter/anthropic/claude-sonnet-4.6",
        "openrouter/anthropic/claude-haiku-4.5",
        "openrouter/google/gemini-2.5-pro",
        "openrouter/deepseek/deepseek-chat",
    ])
    def test_supported_families(self, model):
        # OpenRouter forwards cache_control for these families.
        assert _supports_cache_control(model) is True

    @pytest.mark.parametrize("model", [
        "openrouter/openai/gpt-5",
        "ollama_chat/qwen3.5:35b-a3b-q4_K_M",
        "",
        None,
    ])
    def test_unsupported(self, model):
        assert _supports_cache_control(model) is False


class TestInjectCacheControl:
    _MODEL = "openrouter/anthropic/claude-sonnet-4.6"

    def _long_system(self, chars=5000):
        return "You are Claude. " + ("lorem ipsum " * (chars // 12))[:chars]

    def test_long_system_becomes_block_form(self):
        sys_content = self._long_system()
        msgs = [{"role": "system", "content": sys_content},
                {"role": "user", "content": "hi"}]
        out = inject_cache_control(msgs, self._MODEL)
        sys_out = out[0]
        assert sys_out["role"] == "system"
        assert isinstance(sys_out["content"], list)
        assert sys_out["content"][0]["type"] == "text"
        assert sys_out["content"][0]["cache_control"] == {"type": "ephemeral"}
        assert sys_out["content"][0]["text"] == sys_content

    def test_short_system_stays_string(self):
        msgs = [{"role": "system", "content": "brief"},
                {"role": "user", "content": "hi"}]
        out = inject_cache_control(msgs, self._MODEL)
        assert out[0]["content"] == "brief"
        assert isinstance(out[0]["content"], str)

    def test_unsupported_model_is_noop(self):
        sys_content = self._long_system()
        msgs = [{"role": "system", "content": sys_content}]
        out = inject_cache_control(msgs, "openrouter/openai/gpt-5")
        assert out is msgs  # returned unchanged

    def test_user_and_assistant_messages_untouched(self):
        sys_content = self._long_system()
        msgs = [
            {"role": "system", "content": sys_content},
            {"role": "user", "content": "user text"},
            {"role": "assistant", "content": "assistant text"},
        ]
        out = inject_cache_control(msgs, self._MODEL)
        assert out[1]["content"] == "user text"
        assert out[2]["content"] == "assistant text"

    def test_empty_messages_safe(self):
        assert inject_cache_control([], self._MODEL) == []

    def test_non_list_messages_returned_unchanged(self):
        # crewai.LLM.call accepts a bare string too — must not corrupt it.
        assert inject_cache_control("just a string", self._MODEL) == "just a string"

    def test_only_first_system_injected(self):
        sys_content = self._long_system()
        msgs = [
            {"role": "system", "content": sys_content},
            {"role": "system", "content": sys_content},
            {"role": "user", "content": "hi"},
        ]
        out = inject_cache_control(msgs, self._MODEL)
        assert isinstance(out[0]["content"], list)
        assert isinstance(out[1]["content"], str)

    def test_no_system_message_returned_unchanged(self):
        msgs = [{"role": "user", "content": "hi"}]
        out = inject_cache_control(msgs, self._MODEL)
        assert out == msgs
