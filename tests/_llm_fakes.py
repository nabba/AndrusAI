"""Shared fakes for the OpenRouter+Ollama LLM-consolidation tests.

After the consolidation, raw LLM callers use
``app.llm_factory.chat_completion_for_role(role).create(...)``, which
returns a litellm ``ModelResponse`` in OpenAI shape — the assistant
text is ``resp.choices[0].message.content``.  These helpers build a
minimal stand-in and patch the factory entry point so no real network
call goes out.

Replaces the per-test ``_stub_anthropic`` / fake-``messages.create``
fixtures that mocked the retired native Anthropic surface.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Optional


def openai_response(text: str):
    """Return a minimal OpenAI-shaped chat-completion response."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))]
    )


class FakeChatHandle:
    """Stand-in for ``ChatCompletionHandle``.

    Records the last ``create`` kwargs on ``.captured``; returns an
    OpenAI-shaped response carrying ``text`` (or raises ``raises``).
    """

    def __init__(self, text: str = "", raises: Optional[BaseException] = None):
        # Public + mutable so a fixture can hand back one handle and let
        # each test set the reply (or error) it wants.
        self.text = text
        self.raises = raises
        self.captured: dict = {}

    def create(self, **kwargs):
        self.captured.update(kwargs)
        if self.raises is not None:
            raise self.raises
        return openai_response(self.text)


def patch_chat_completion(
    monkeypatch,
    text: str = "",
    raises: Optional[BaseException] = None,
) -> FakeChatHandle:
    """Patch ``app.llm_factory.chat_completion_for_role`` to return a
    :class:`FakeChatHandle`.

    Works for lazy ``from app.llm_factory import chat_completion_for_role``
    call sites because the name is resolved from the (patched) module at
    call time.  Returns the handle so the test can inspect ``.captured``.
    """
    handle = FakeChatHandle(text=text, raises=raises)
    monkeypatch.setattr(
        "app.llm_factory.chat_completion_for_role",
        lambda *a, **kw: handle,
    )
    return handle
