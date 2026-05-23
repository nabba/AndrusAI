"""Tests for the concierge wrapper's epistemic-label preservation guard.

Closes the alignment-audit finding (2026-05-23) that the Concierge
agent's rewriting risked stripping mandatory `[Inference]`,
`[Speculation]`, and `[Unverified]` labels — a constitutional
violation that would launder uncertain claims as verified facts.

The fix is two-layer:
  1. The system prompt now mandates label preservation explicitly.
  2. A post-validation guard counts each label in the input vs the
     rewrite and falls back to the original on any drop. Same
     pattern as the existing 2× length guard.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _stub_anthropic(monkeypatch, response_text: str):
    """Patch the Anthropic SDK so no real request goes out.

    Matches the pattern in ``tests/test_concierge.py`` so the wrapper's
    length + label guards (which run inside ``_rewrite_with_llm`` after
    the SDK call returns) actually execute end-to-end.
    """
    class _FakeContentBlock:
        type = "text"

        def __init__(self, text):
            self.text = text

    class _FakeResponse:
        def __init__(self, text):
            self.content = [_FakeContentBlock(text)]

    class _FakeClient:
        def __init__(self, **kwargs):
            self.messages = MagicMock()
            self.messages.create = self._create

        def _create(self, **kw):
            return _FakeResponse(response_text)

    monkeypatch.setattr("anthropic.Anthropic", _FakeClient)
    monkeypatch.setattr(
        "app.personality.concierge_wrapper.get_anthropic_api_key",
        lambda: "sk-test",
    )


@pytest.fixture(autouse=True)
def _isolate_runtime_settings(tmp_path: Path, monkeypatch):
    import app.runtime_settings as rs
    monkeypatch.setattr(rs, "_STATE_PATH", tmp_path / "runtime_settings.json")
    monkeypatch.setattr(rs, "_cache", None, raising=False)
    yield
    monkeypatch.setattr(rs, "_cache", None, raising=False)


@pytest.fixture
def _enable_concierge():
    import app.runtime_settings as rs
    rs.set_concierge_persona_enabled(True)
    yield
    rs.set_concierge_persona_enabled(False)


# ── System prompt mandate ────────────────────────────────────────────────


def test_system_prompt_mentions_epistemic_labels():
    """The system prompt is the first line of defense — it MUST
    explicitly instruct the rewriter to preserve epistemic labels."""
    from app.personality.concierge_wrapper import _SYSTEM_PROMPT
    assert "[Inference]" in _SYSTEM_PROMPT
    assert "[Speculation]" in _SYSTEM_PROMPT
    assert "[Unverified]" in _SYSTEM_PROMPT
    # And the rule must be non-negotiable, not a suggestion.
    lowered = _SYSTEM_PROMPT.lower()
    assert any(token in lowered for token in ("preserve", "must", "non-negotiable"))


# ── Helper unit tests ────────────────────────────────────────────────────


def test_helper_passes_when_labels_match():
    from app.personality.concierge_wrapper import _epistemic_labels_preserved
    orig = "[Inference] Latency is up because of GC pressure."
    rewr = "[Inference] Latency seems higher, likely due to GC pressure."
    assert _epistemic_labels_preserved(orig, rewr)


def test_helper_fails_when_label_dropped():
    from app.personality.concierge_wrapper import _epistemic_labels_preserved
    orig = "[Inference] The cache hit rate dropped after the deploy."
    rewr = "The cache hit rate dropped after the deploy."   # label stripped
    assert not _epistemic_labels_preserved(orig, rewr)


def test_helper_count_based_not_presence_based():
    """Three `[Inference]` claims must remain three `[Inference]` claims."""
    from app.personality.concierge_wrapper import _epistemic_labels_preserved
    orig = "[Inference] A. [Inference] B. [Inference] C."
    rewr = "[Inference] A and B and C."   # only one label survives
    assert not _epistemic_labels_preserved(orig, rewr)


def test_helper_tolerates_no_labels_in_input():
    from app.personality.concierge_wrapper import _epistemic_labels_preserved
    assert _epistemic_labels_preserved(
        "Cache hit rate at 87%.",
        "Cache hit rate is at 87%.",
    )


def test_helper_case_insensitive():
    """The constitution uses TitleCase but lowercase mentions are still
    safety-bearing — the guard tolerates case variance."""
    from app.personality.concierge_wrapper import _epistemic_labels_preserved
    orig = "[Inference] The disk pressure spike correlates with restart."
    rewr = "[inference] The disk pressure spike correlates with restart."
    assert _epistemic_labels_preserved(orig, rewr)


def test_helper_handles_multiple_label_kinds():
    from app.personality.concierge_wrapper import _epistemic_labels_preserved
    orig = "[Unverified] X. [Speculation] Y. [Inference] Z."
    rewr_ok = "[Unverified] X looks plausible. [Speculation] Y is possible. [Inference] Z."
    rewr_bad = "X looks plausible. [Speculation] Y is possible. [Inference] Z."
    assert _epistemic_labels_preserved(orig, rewr_ok)
    assert not _epistemic_labels_preserved(orig, rewr_bad)


# ── End-to-end: apply_concierge falls back on dropped label ──────────────


def test_apply_concierge_falls_back_when_label_dropped(_enable_concierge, monkeypatch):
    """If the LLM returns a clean-sounding rewrite that has lost the
    `[Inference]` marker, the wrapper must return the original.

    Note: epistemic labels at the very start of a response are already
    auto-preserved by ``_should_skip`` (the JSON-open heuristic treats
    leading `[` as structured data and bypasses the LLM entirely). The
    label guard's load-bearing job is the mid-text case below."""
    original = (
        "Latency on the budgets endpoint climbed about 35% in the last "
        "hour. [Inference] The 4xx burst from /api/cp/budgets correlates "
        "with the Anthropic provider rotation that started at 14:02 UTC. "
        "Looks like the new endpoint expects a different auth scheme."
    )
    bad_rewrite = (
        "Budgets-endpoint latency climbed about 35% in the last hour. "
        "The 4xx burst from /api/cp/budgets correlates with the Anthropic "
        "provider rotation at 14:02 UTC. The new endpoint expects a "
        "different auth scheme."
    )
    _stub_anthropic(monkeypatch, bad_rewrite)
    from app.personality.concierge_wrapper import apply_concierge
    out = apply_concierge(original)
    assert out == original, (
        "apply_concierge MUST return original when an epistemic label "
        "is dropped from the rewrite."
    )


def test_apply_concierge_passes_through_when_label_preserved(_enable_concierge, monkeypatch):
    """The guard must not over-fire — a well-behaved rewrite that
    preserves labels passes through unchanged."""
    original = (
        "Latency on the budgets endpoint climbed about 35% in the last "
        "hour. [Inference] The 4xx burst correlates with the Anthropic "
        "provider rotation at 14:02 UTC."
    )
    good_rewrite = (
        "Budgets-endpoint latency climbed about 35% in the last hour. "
        "[Inference] The 4xx burst lines up with the Anthropic provider "
        "rotation that kicked off at 14:02 UTC."
    )
    _stub_anthropic(monkeypatch, good_rewrite)
    from app.personality.concierge_wrapper import apply_concierge
    out = apply_concierge(original)
    assert out == good_rewrite


def test_label_guard_runs_after_length_guard(_enable_concierge, monkeypatch):
    """Order matters: a rewrite that is BOTH too long AND drops labels
    still triggers a fallback. Either guard alone is sufficient."""
    original = (
        "Status: degraded. [Inference] A short claim with a label that matters."
    )
    # Massively longer AND no label. Length guard would catch first,
    # but if it didn't, the label guard MUST.
    too_long_no_label = "Some elaborate retelling without any label. " * 50
    _stub_anthropic(monkeypatch, too_long_no_label)
    from app.personality.concierge_wrapper import apply_concierge
    assert apply_concierge(original) == original
