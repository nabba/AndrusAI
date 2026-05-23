"""Pin the PhenomenalLanguageLinter wire-in for ``threads/approaches.py``.

Added 2026-05-23 audit follow-up. The LLM call in ``_llm_distill`` was
producing identity-adjacent prose (closure summary lands in the
lessons_learned KB and is consulted at future thread creation) without
the same mechanical second-guard that every other identity-shaping LLM
producer uses. The wire returns "" on linter HARD_FAIL, which makes
the outer ``distill_on_closure`` fall back to the deterministic body
builder.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class _FakeContentBlock:
    def __init__(self, text):
        self.text = text
        self.type = "text"


class _FakeMessage:
    def __init__(self, text):
        self.content = [_FakeContentBlock(text)]


def _fake_anthropic_client(reply_text: str):
    """Return an Anthropic client stand-in that always returns
    ``reply_text`` from ``messages.create``."""
    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = MagicMock(
        return_value=_FakeMessage(reply_text),
    )
    return client


@pytest.fixture
def llm_distill_setup(monkeypatch):
    """Provide a clean LLM seam — patch the anthropic import inside
    ``threads.approaches`` so the test can drive the LLM reply."""
    import sys

    # Make sure the anthropic module appears importable inside the
    # function (it imports lazily) but the constructor is fake.
    fake_anthropic = MagicMock()
    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)
    # Bypass the daily-cap gate.
    from app import llm_anthropic_budget
    monkeypatch.setattr(
        llm_anthropic_budget, "call_or_skip",
        lambda **kwargs: True,
    )
    return fake_anthropic


def _build_probe_thread():
    """Build a minimum-viable Thread suitable for distill_on_closure."""
    from datetime import datetime, timezone
    from app.threads.models import Thread, ThreadStatus

    now = datetime.now(timezone.utc).isoformat()
    return Thread(
        id="probe-thread-id",
        created_at=now,
        title="probe thread",
        description="x",
        last_touched_at=now,
        status=ThreadStatus.RESOLVED,
        notes=["resolved by approach X"],
        blockers=["dependency Y"],
    )


def test_llm_distill_rejects_phenomenal_first_person(llm_distill_setup):
    """If the LLM returns phenomenal first-person prose, the linter
    HARD_FAIL must cause _llm_distill to return "" (caller falls back
    to deterministic body)."""
    from app.threads.approaches import _llm_distill

    # First-person phenomenal claim — matches the linter's
    # _FIRST_PERSON_PHENOMENAL HARD_FAIL pattern. "I feel that …" is
    # specifically EXEMPTED by the linter (the negative lookahead for
    # `" that"`), so to trigger we use "I am curious" which matches
    # the phenomenal-state regex.
    bad_reply = (
        "I am curious about why approach X resolved the thread. "
        "The dependency-resolution step was the missing piece."
    )
    llm_distill_setup.Anthropic.return_value = _fake_anthropic_client(bad_reply)

    out = _llm_distill(
        _build_probe_thread(),
        "Question: probe thread\nClosed as: resolved",
    )
    assert out == "", (
        f"Linter HARD_FAIL must collapse the LLM output to empty so "
        f"distill_on_closure falls back to the deterministic body; "
        f"got: {out!r}"
    )


def test_llm_distill_accepts_third_person_summary(llm_distill_setup):
    """Third-person prose without phenomenal first-person claims must
    pass through unchanged."""
    from app.threads.approaches import _llm_distill

    good_reply = (
        "Approach X resolved the thread by adding the missing "
        "dependency-resolution step. Earlier attempts with approach Y "
        "were blocked by dependency Y."
    )
    llm_distill_setup.Anthropic.return_value = _fake_anthropic_client(good_reply)

    out = _llm_distill(
        _build_probe_thread(),
        "Question: probe thread\nClosed as: resolved",
    )
    assert out == good_reply


def test_distill_on_closure_falls_back_to_deterministic_on_linter_fail(
    llm_distill_setup, monkeypatch, tmp_path,
):
    """End-to-end: the outer ``distill_on_closure`` must produce a
    non-empty summary even when the LLM returns phenomenal prose. The
    fallback path returns the deterministic-body content."""
    from app.threads import approaches, store
    store.reset_for_tests(tmp_path / "threads")

    monkeypatch.setattr(approaches, "_llm_enabled", lambda: True)
    bad_reply = "I experience this thread as having been straightforward."
    llm_distill_setup.Anthropic.return_value = _fake_anthropic_client(bad_reply)

    thread = _build_probe_thread()
    out = approaches.distill_on_closure(thread)

    # The deterministic body always starts with "Question:" — that's
    # the signature of the fallback path.
    assert out.startswith("Question:"), (
        f"Expected deterministic-body fallback (starts with 'Question:'), "
        f"got: {out!r}"
    )
    # And the phenomenal claim must NOT appear anywhere in the output.
    assert "I experience" not in out
