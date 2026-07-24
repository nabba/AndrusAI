"""Tests for the provider-validation guard in ``rehydrate_catalog``.

Origin: 2026-07-24 finding D1 (reports/ANSWER_QUALITY_DIAGNOSIS_2026-07-24.md).
A promoted ``control_plane.discovered_models`` row surviving from before the
OpenRouter+Ollama consolidation (2026-05-29) carried ``provider="anthropic"``
for ``claude-haiku-4-5``. ``rehydrate_catalog`` re-inserted it into CATALOG
on every boot; ``app.llm_factory._construct_from_entry`` only knows how to
build "openrouter"/"ollama" entries, so every ``writing``-role selection
that landed on this promoted model failed construction with
``unknown_provider`` and silently fell back further down the chain — a
wasted construction attempt on every single call, with the wrong model
reported as selected.

These tests pin: a promoted row with an unbuildable provider is skipped
(not inserted) and logged, while a valid-provider row still rehydrates
normally.
"""
from __future__ import annotations

import app.llm_rehydrate as llm_rehydrate


def _row(model_id, provider, **extra):
    base = {
        "model_id": model_id,
        "provider": provider,
        "display_name": model_id,
        "context_window": 128000,
        "cost_input_per_m": 1.0,
        "cost_output_per_m": 5.0,
        "multimodal": False,
        "tool_calling": True,
        "promoted_tier": "mid",
        "promoted_roles": ["writing"],
        "benchmark_score": 0.8,
    }
    base.update(extra)
    return base


def test_stale_anthropic_provider_row_is_skipped_not_inserted(monkeypatch):
    rows = [_row("anthropic/claude-haiku-4-5", "anthropic")]
    monkeypatch.setattr(
        "app.control_plane.db.execute", lambda *a, **kw: rows,
    )
    added_calls = []
    monkeypatch.setattr(
        "app.llm_discovery._add_to_runtime_catalog",
        lambda payload, roles: added_calls.append(payload),
    )
    monkeypatch.setattr("app.llm_catalog.CATALOG", {})

    added = llm_rehydrate.rehydrate_catalog(force=True)

    assert added == 0
    assert added_calls == []  # never reached the factory-facing insert


def test_valid_openrouter_provider_row_still_rehydrates(monkeypatch):
    rows = [_row("anthropic/claude-haiku-4.5", "openrouter")]
    monkeypatch.setattr(
        "app.control_plane.db.execute", lambda *a, **kw: rows,
    )
    added_calls = []
    monkeypatch.setattr(
        "app.llm_discovery._add_to_runtime_catalog",
        lambda payload, roles: added_calls.append(payload),
    )
    monkeypatch.setattr("app.llm_catalog.CATALOG", {})

    added = llm_rehydrate.rehydrate_catalog(force=True)

    assert added == 1
    assert len(added_calls) == 1
    assert added_calls[0]["provider"] == "openrouter"


def test_valid_ollama_provider_row_still_rehydrates(monkeypatch):
    rows = [_row("qwen3.5:35b-a3b-q4_K_M", "ollama")]
    monkeypatch.setattr(
        "app.control_plane.db.execute", lambda *a, **kw: rows,
    )
    added_calls = []
    monkeypatch.setattr(
        "app.llm_discovery._add_to_runtime_catalog",
        lambda payload, roles: added_calls.append(payload),
    )
    monkeypatch.setattr("app.llm_catalog.CATALOG", {})

    added = llm_rehydrate.rehydrate_catalog(force=True)

    assert added == 1


def test_mixed_batch_skips_only_the_invalid_provider_row(monkeypatch):
    rows = [
        _row("anthropic/claude-haiku-4-5", "anthropic"),
        _row("z-ai/glm-5.2", "openrouter"),
    ]
    monkeypatch.setattr(
        "app.control_plane.db.execute", lambda *a, **kw: rows,
    )
    added_calls = []
    monkeypatch.setattr(
        "app.llm_discovery._add_to_runtime_catalog",
        lambda payload, roles: added_calls.append(payload),
    )
    monkeypatch.setattr("app.llm_catalog.CATALOG", {})

    added = llm_rehydrate.rehydrate_catalog(force=True)

    assert added == 1
    assert added_calls[0]["model_id"] == "z-ai/glm-5.2"


def test_missing_provider_defaults_to_openrouter_and_is_valid(monkeypatch):
    row = _row("z-ai/glm-5.2", None)
    row.pop("provider")
    monkeypatch.setattr(
        "app.control_plane.db.execute", lambda *a, **kw: [row],
    )
    added_calls = []
    monkeypatch.setattr(
        "app.llm_discovery._add_to_runtime_catalog",
        lambda payload, roles: added_calls.append(payload),
    )
    monkeypatch.setattr("app.llm_catalog.CATALOG", {})

    added = llm_rehydrate.rehydrate_catalog(force=True)

    assert added == 1
    assert added_calls[0]["provider"] == "openrouter"
