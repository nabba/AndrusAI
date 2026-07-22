"""
test_concierge — wrapper toggle, skip heuristics, LLM call, length guard.

The Anthropic SDK is monkey-patched so no real API call happens.
"""
from __future__ import annotations

import pytest

from tests._llm_fakes import patch_chat_completion


@pytest.fixture(autouse=True)
def _isolate_runtime_settings(tmp_path, monkeypatch):
    import app.runtime_settings as rs
    monkeypatch.setattr(rs, "_STATE_PATH", tmp_path / "runtime_settings.json")
    monkeypatch.setattr(rs, "_cache", None, raising=False)
    yield
    monkeypatch.setattr(rs, "_cache", None, raising=False)


# ── Toggle gating ─────────────────────────────────────────────────────────

def test_passthrough_when_disabled(monkeypatch):
    from app.personality.concierge_wrapper import apply_concierge
    # Default state (toggle off) — no rewrite, no LLM call.
    monkeypatch.setattr(
        "app.personality.concierge_wrapper._rewrite_with_llm",
        lambda *a, **kw: pytest.fail("LLM should not be called when disabled"),
    )
    result = apply_concierge("Some long enough conversational reply that would otherwise be rewritten.")
    assert result.startswith("Some long enough")


# ── Skip heuristics ───────────────────────────────────────────────────────

@pytest.fixture
def _enable_concierge():
    """Turn the toggle on for the duration of the test."""
    import app.runtime_settings as rs
    rs.set_concierge_persona_enabled(True)
    yield
    rs.set_concierge_persona_enabled(False)


def test_skip_too_short(_enable_concierge, monkeypatch):
    from app.personality.concierge_wrapper import apply_concierge
    monkeypatch.setattr(
        "app.personality.concierge_wrapper._rewrite_with_llm",
        lambda *a, **kw: pytest.fail("should skip short text"),
    )
    assert apply_concierge("ok") == "ok"


def test_skip_json_payload(_enable_concierge, monkeypatch):
    from app.personality.concierge_wrapper import apply_concierge
    payload = '{"crews": [{"crew": "research", "task": "summarize"}]}'
    monkeypatch.setattr(
        "app.personality.concierge_wrapper._rewrite_with_llm",
        lambda *a, **kw: pytest.fail("should skip JSON"),
    )
    assert apply_concierge(payload) == payload


def test_skip_array_payload(_enable_concierge, monkeypatch):
    from app.personality.concierge_wrapper import apply_concierge
    payload = '[{"id": 1}, {"id": 2}, {"id": 3}]'
    monkeypatch.setattr(
        "app.personality.concierge_wrapper._rewrite_with_llm",
        lambda *a, **kw: pytest.fail("should skip JSON array"),
    )
    assert apply_concierge(payload) == payload


def test_skip_fenced_code(_enable_concierge, monkeypatch):
    from app.personality.concierge_wrapper import apply_concierge
    code = "Here is your snippet:\n```python\nprint('hi')\n```"
    monkeypatch.setattr(
        "app.personality.concierge_wrapper._rewrite_with_llm",
        lambda *a, **kw: pytest.fail("should skip fenced code"),
    )
    assert apply_concierge(code) == code


@pytest.mark.parametrize("prefix", [
    "Usage: /skill run <name>",
    "AndrusAI status\n  voice: off",
    "AndrusAI — Signal commands\n\nStatus & info:",
    "Skill registry — save tasks you run repeatedly.",
    "Skills (3 total):",
    "Skill: weekly status",
    "Saved skill 'weekly'.",
    "Deleted skill 'foo'.",
    "✓ done in 4.2s",
    "✗ failed: RuntimeError: nope",
])
def test_skip_known_structured_prefixes(_enable_concierge, monkeypatch, prefix):
    from app.personality.concierge_wrapper import apply_concierge
    monkeypatch.setattr(
        "app.personality.concierge_wrapper._rewrite_with_llm",
        lambda *a, **kw: pytest.fail(f"should skip {prefix!r}"),
    )
    assert apply_concierge(prefix) == prefix


# ── LLM rewrite path ──────────────────────────────────────────────────────

def test_rewrite_replaces_terse_with_warm(_enable_concierge, monkeypatch):
    from app.personality.concierge_wrapper import apply_concierge
    handle = patch_chat_completion(
        monkeypatch, "Done — research crew is on it, about 18 seconds.",
    )
    original = "Routed to research crew. ETA 18s. 3 sources will be checked."
    rewritten = apply_concierge(original)
    assert "research crew" in rewritten
    assert rewritten != original
    # Ensure the fake factory call was actually made with the concierge system prompt.
    assert handle.captured.get("system") is not None
    assert "concierge" in (handle.captured.get("system") or "").lower()


def test_rewrite_falls_back_when_too_long(_enable_concierge, monkeypatch):
    from app.personality.concierge_wrapper import apply_concierge
    very_long = "warm " * 200  # ~1000 chars
    patch_chat_completion(monkeypatch, very_long)
    original = "Routed to research crew. ETA 18s."
    # Length guard kicks in; concierge falls back to the original.
    assert apply_concierge(original) == original


def test_rewrite_falls_back_on_empty_response(_enable_concierge, monkeypatch):
    from app.personality.concierge_wrapper import apply_concierge
    patch_chat_completion(monkeypatch, "")
    original = "Routed to research crew. ETA 18s. 3 sources will be checked."
    assert apply_concierge(original) == original


def test_rewrite_falls_back_when_no_model_available(_enable_concierge, monkeypatch):
    from app.personality.concierge_wrapper import apply_concierge
    from app.llm_factory import NoWorkingModelAvailable
    # No working model (e.g. OPENROUTER_API_KEY unset / all candidates dead)
    # surfaces as NoWorkingModelAvailable from the factory; concierge degrades
    # to the original text.
    patch_chat_completion(
        monkeypatch, raises=NoWorkingModelAvailable("cheap-vetting", []),
    )
    original = "Routed to research crew. ETA 18s. 3 sources will be checked."
    assert apply_concierge(original) == original


def test_rewrite_falls_back_when_llm_raises(_enable_concierge, monkeypatch):
    from app.personality.concierge_wrapper import apply_concierge
    patch_chat_completion(monkeypatch, raises=RuntimeError("API down"))
    original = "Routed to research crew. ETA 18s. 3 sources will be checked."
    assert apply_concierge(original) == original  # fallback, no raise


def test_empty_input_passes_through(_enable_concierge):
    from app.personality.concierge_wrapper import apply_concierge
    assert apply_concierge("") == ""
    assert apply_concierge("   ") == "   "


def test_integrity_sensitive_response_bypasses_rewrite(_enable_concierge, monkeypatch):
    from app.personality.concierge_wrapper import apply_concierge

    monkeypatch.setattr(
        "app.personality.concierge_wrapper._rewrite_with_llm",
        lambda *a, **kw: pytest.fail("protected research must not be rewritten"),
    )
    original = (
        "Research finding with source https://authority.example/report and "
        "material details that must survive delivery unchanged."
    )
    assert apply_concierge(original, integrity_sensitive=True) == original


def test_rewrite_falls_back_when_substantially_shortened(_enable_concierge, monkeypatch):
    from app.personality.concierge_wrapper import apply_concierge

    original = "Evidence-bearing explanatory sentence. " * 30
    patch_chat_completion(monkeypatch, "A much shorter paraphrase that omits most details.")
    assert apply_concierge(original) == original


def test_rewrite_falls_back_when_source_url_is_dropped(_enable_concierge, monkeypatch):
    from app.personality.concierge_wrapper import apply_concierge

    original = (
        "The source is https://authority.example/report and it supports the "
        "specific conclusion described in this response."
    )
    patch_chat_completion(
        monkeypatch,
        "The source supports the specific conclusion described in this response.",
    )
    assert apply_concierge(original) == original
