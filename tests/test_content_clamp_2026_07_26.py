"""Length clamps on the answer path must record what they drop.

Motivation (reports/GATE_DIAGNOSIS_2026-07-25.md, Addendum 10): "max_tokens
truncation removed the source list" was graded INFERRED and never tested. The
token ledger then showed **zero** completions pinned at the research caps
(3000/3500) across 62,675 calls in 14 days — a character clamp would never show
up there, and none of them recorded anything. These tests pin the recording, not
the limits.
"""

from __future__ import annotations

import pytest

content_clamp = pytest.importorskip("app.content_clamp")


@pytest.fixture(autouse=True)
def _clean():
    content_clamp.reset_stats()
    yield
    content_clamp.reset_stats()


def test_short_text_passes_through_unchanged_and_uncounted():
    assert content_clamp.clamp("hello", 100, what="a->b") == "hello"
    assert content_clamp.stats() == {}


def test_exactly_at_limit_is_not_a_clamp():
    assert content_clamp.clamp("x" * 50, 50, what="a->b") == "x" * 50
    assert content_clamp.stats() == {}


def test_overflow_is_cut_and_counted():
    out = content_clamp.clamp("x" * 130, 100, what="draft->critique")
    assert out == "x" * 100
    entry = content_clamp.stats()["draft->critique"]
    assert entry == {
        "times_clamped": 1,
        "chars_dropped": 30,
        "largest_drop": 30,
    }


def test_counters_accumulate_per_hop():
    content_clamp.clamp("x" * 110, 100, what="a->b")
    content_clamp.clamp("x" * 150, 100, what="a->b")
    content_clamp.clamp("x" * 120, 100, what="c->d")
    stats = content_clamp.stats()
    assert stats["a->b"] == {
        "times_clamped": 2, "chars_dropped": 60, "largest_drop": 50,
    }
    assert stats["c->d"]["times_clamped"] == 1


def test_overflow_is_logged_with_the_hop_name(caplog):
    with caplog.at_level("WARNING"):
        content_clamp.clamp("x" * 200, 10, what="investigation->draft")
    messages = [r.getMessage() for r in caplog.records]
    assert any("investigation->draft" in m for m in messages), messages
    assert any("dropped 190" in m for m in messages), messages


def test_none_and_empty_are_safe():
    assert content_clamp.clamp(None, 10, what="a->b") == ""
    assert content_clamp.clamp("", 10, what="a->b") == ""
    assert content_clamp.stats() == {}


def test_zero_and_negative_limits_do_not_crash():
    assert content_clamp.clamp("abc", 0, what="a->b") == ""
    assert content_clamp.clamp("abc", -5, what="a->b") == ""


def test_stats_are_a_copy_not_the_live_dict():
    content_clamp.clamp("x" * 20, 5, what="a->b")
    snapshot = content_clamp.stats()
    snapshot["a->b"]["times_clamped"] = 999
    assert content_clamp.stats()["a->b"]["times_clamped"] == 1


def test_clamp_is_threadsafe_under_concurrent_use():
    import threading

    def worker():
        for _ in range(50):
            content_clamp.clamp("x" * 20, 10, what="shared")

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert content_clamp.stats()["shared"]["times_clamped"] == 400
    assert content_clamp.stats()["shared"]["chars_dropped"] == 4000


# ── The research path actually uses it ──────────────────────────────────────

def test_research_path_clamps_are_instrumented():
    """Both report-path clamps must route through the recorder, not `[:n]`."""
    import pathlib

    src = (
        pathlib.Path(content_clamp.__file__).resolve().parent
        / "research" / "run.py"
    ).read_text()
    assert 'clamp(investigation, 4000, what="investigation->draft")' in src
    assert 'clamp(draft, 8000, what="draft->critique")' in src
    assert "investigation[:4000]" not in src
    assert "draft[:8000]" not in src


def test_focused_completion_records_max_tokens_truncation():
    """finish_reason=='length' is the only signal the cost ledger cannot give."""
    import pathlib

    src = (
        pathlib.Path(content_clamp.__file__).resolve().parent
        / "research" / "run.py"
    ).read_text()
    assert "finish_reason" in src
    assert "max_tokens=%d ceiling" in src
