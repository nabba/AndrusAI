"""Contract tests for the raw OpenAI-compatible completion surface
(``chat_completion_for_role`` / ``ChatCompletionHandle``) added in the
OpenRouter+Ollama consolidation, Step 1.

These pin the behaviour the 19 migrated raw callers depend on:
  * ``system=`` is prepended as a role="system" message
  * extra kwargs (``temperature`` etc.) pass through to litellm
  * the OpenRouter per-call budget gate + Stealth exclusion fire on the
    OpenRouter path
  * call outcomes feed the model-id health cache (mark_alive / mark_dead)
  * an exhausted chain surfaces ``NoWorkingModelAvailable``

The module is import-guarded: on a host without the gateway deps
(pydantic_settings etc.) the whole file skips cleanly, matching the
rest of the suite which exercises these paths in the CI container.
"""
import sys
import types

import pytest

llm_factory = pytest.importorskip("app.llm_factory")

from app.llm_factory import (  # noqa: E402
    _RawTarget,
    _apply_openrouter_provider_exclusion,
    chat_completion_for_role,
    NoWorkingModelAvailable,
)


# ── _apply_openrouter_provider_exclusion (pure helper) ───────────────

def test_stealth_exclusion_default(monkeypatch):
    monkeypatch.delenv("OPENROUTER_IGNORE_PROVIDERS", raising=False)
    kw: dict = {}
    _apply_openrouter_provider_exclusion(kw)
    assert kw["extra_body"]["provider"]["ignore"] == ["Stealth"]


def test_stealth_exclusion_merges_without_clobbering(monkeypatch):
    monkeypatch.setenv("OPENROUTER_IGNORE_PROVIDERS", "Stealth,Foo")
    kw = {"extra_body": {"provider": {"ignore": ["Foo"], "order": ["x"]}}}
    _apply_openrouter_provider_exclusion(kw)
    prov = kw["extra_body"]["provider"]
    assert prov["ignore"] == ["Foo", "Stealth"]  # existing kept, no dup
    assert prov["order"] == ["x"]                 # unrelated keys untouched


def test_stealth_exclusion_empty_env_disables(monkeypatch):
    monkeypatch.setenv("OPENROUTER_IGNORE_PROVIDERS", "")
    kw: dict = {}
    _apply_openrouter_provider_exclusion(kw)
    assert "extra_body" not in kw


# ── ChatCompletionHandle.create ──────────────────────────────────────

class _FakeProbe:
    def __init__(self):
        self.alive: list[tuple[str, str]] = []
        self.dead: list[tuple[str, str, str]] = []

    def classify_failure(self, exc):
        return "model not found" if "404" in str(exc) else None

    def mark_alive(self, provider, model_id):
        self.alive.append((provider, model_id))

    def mark_dead(self, provider, model_id, reason):
        self.dead.append((provider, model_id, reason))


def _install_fakes(monkeypatch, *, completion, target, probe=None):
    """Wire fake litellm / budget / cost-exceptions / probe / resolver."""
    probe = probe or _FakeProbe()
    monkeypatch.setitem(sys.modules, "litellm",
                        types.SimpleNamespace(completion=completion))
    monkeypatch.setitem(sys.modules, "app.llm_openrouter_budget",
                        types.SimpleNamespace(pre_check=lambda **k: None))

    class _Cap(Exception):
        ...
    monkeypatch.setitem(sys.modules, "app.llm_cost_exceptions",
                        types.SimpleNamespace(CapExceededError=_Cap))
    monkeypatch.setattr(llm_factory, "llm_factory_probe", probe)
    monkeypatch.setattr(llm_factory, "_resolve_raw_target",
                        lambda role, task_hint="": target)
    return probe


def _or_target():
    return _RawTarget(
        catalog_key="claude-sonnet-4.6", provider="openrouter",
        model_id="openrouter/anthropic/claude-sonnet-4.6",
        api_key="or-key", api_base=None, cost_in=1.0, cost_out=5.0,
        health_provider="anthropic", bare="claude-sonnet-4.6",
    )


def test_create_prepends_system_and_forwards_temperature(monkeypatch):
    captured: dict = {}

    def _completion(**kw):
        captured.update(kw)
        return "RESP"

    _install_fakes(monkeypatch, completion=_completion, target=_or_target())

    resp = chat_completion_for_role("cheap-vetting").create(
        system="SYS", messages=[{"role": "user", "content": "hi"}],
        max_tokens=200, temperature=0.9,
    )
    assert resp == "RESP"
    assert captured["model"] == "openrouter/anthropic/claude-sonnet-4.6"
    assert captured["messages"][0] == {"role": "system", "content": "SYS"}
    assert captured["messages"][1] == {"role": "user", "content": "hi"}
    assert captured["max_tokens"] == 200
    assert captured["temperature"] == 0.9          # passthrough kwarg
    assert captured["api_key"] == "or-key"
    # Stealth exclusion applied on the OpenRouter path.
    assert captured["extra_body"]["provider"]["ignore"] == ["Stealth"]


def test_create_marks_alive_on_success(monkeypatch):
    probe = _install_fakes(
        monkeypatch, completion=lambda **kw: "ok", target=_or_target(),
    )
    chat_completion_for_role("media").create(
        messages=[{"role": "user", "content": "x"}],
    )
    assert probe.alive == [("anthropic", "claude-sonnet-4.6")]
    assert probe.dead == []


def test_create_marks_dead_on_model_not_found(monkeypatch):
    def _boom(**kw):
        raise RuntimeError("Error code: 404 not_found_error")

    probe = _install_fakes(monkeypatch, completion=_boom, target=_or_target())
    with pytest.raises(RuntimeError):
        chat_completion_for_role("media").create(
            messages=[{"role": "user", "content": "x"}],
        )
    assert probe.dead and probe.dead[0][0] == "anthropic"
    assert probe.dead[0][1] == "claude-sonnet-4.6"
    assert probe.alive == []


def test_create_propagates_no_working_model(monkeypatch):
    def _raise(role, task_hint=""):
        raise NoWorkingModelAvailable(role, [])

    monkeypatch.setattr(llm_factory, "_resolve_raw_target", _raise)
    with pytest.raises(NoWorkingModelAvailable):
        chat_completion_for_role("research").create(
            messages=[{"role": "user", "content": "x"}],
        )


def test_ollama_target_uses_api_base_no_key_no_stealth(monkeypatch):
    captured: dict = {}

    def _completion(**kw):
        captured.update(kw)
        return "local"

    target = _RawTarget(
        catalog_key="qwen", provider="ollama",
        model_id="ollama_chat/qwen3.5", api_key=None,
        api_base="http://127.0.0.1:11434", cost_in=0.0, cost_out=0.0,
        health_provider="ollama", bare="ollama_chat/qwen3.5",
    )
    _install_fakes(monkeypatch, completion=_completion, target=target)
    chat_completion_for_role("coding").create(
        messages=[{"role": "user", "content": "x"}], max_tokens=128,
    )
    assert captured["model"] == "ollama_chat/qwen3.5"
    assert captured["api_base"] == "http://127.0.0.1:11434"
    assert "api_key" not in captured        # local needs no key
    assert "extra_body" not in captured     # Stealth is OpenRouter-only
