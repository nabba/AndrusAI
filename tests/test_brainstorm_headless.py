"""Host-safe tests for app.brainstorm.headless.

The gatherer, novelty assessor, and aesthetic scorer are all injected, so no
LLM / ChromaDB / crewai is touched.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.brainstorm import headless as H
from app.brainstorm.headless import Hypothesis


# ── fakes ───────────────────────────────────────────────────────────────────


@dataclass
class _Resp:
    role: str
    text: str
    error: str | None = None


def _gather_returning(responses):
    captured = {}

    def gather(*, technique_title, topic, step_prompt, roster, spent_so_far_usd=0.0):
        captured.update(
            technique_title=technique_title, topic=topic,
            step_prompt=step_prompt, roster=roster, spent=spent_so_far_usd,
        )
        return list(responses)

    return gather, captured


class _Wrap:
    def __init__(self, value, notes=None):
        self.verdict = value
        self.notes = notes or []


# ── _split_numbered ─────────────────────────────────────────────────────────


def test_split_numbered_dotted():
    assert H._split_numbered("1. alpha\n2. beta\n3. gamma") == ["alpha", "beta", "gamma"]


def test_split_numbered_paren_and_bullets():
    assert H._split_numbered("1) a\n- b\n* c\n• d") == ["a", "b", "c", "d"]


def test_split_numbered_continuation_lines_join():
    text = "1. first idea\n   continues here\n2. second"
    assert H._split_numbered(text) == ["first idea continues here", "second"]


def test_split_numbered_blob_no_markers_is_single_item():
    assert H._split_numbered("just one thought") == ["just one thought"]


def test_split_numbered_empty():
    assert H._split_numbered("") == []
    assert H._split_numbered("   \n  \n") == []


# ── generate_hypotheses ─────────────────────────────────────────────────────


def test_generate_empty_topic_returns_empty():
    gather, _ = _gather_returning([_Resp("coder", "1. x")])
    assert H.generate_hypotheses("  ", gather=gather) == []


def test_generate_maps_and_scores():
    gather, captured = _gather_returning([
        _Resp("researcher", "1. idea-a\n2. idea-b"),
        _Resp("coder", "1. idea-c"),
    ])
    scores = {"idea-a": 0.9, "idea-b": 0.5, "idea-c": 0.7}
    hyps = H.generate_hypotheses(
        "topic",
        gather=gather,
        assess=lambda t: _Wrap("novel"),
        score_fn=lambda t: scores.get(t),
    )
    assert all(isinstance(h, Hypothesis) for h in hyps)
    texts = {h.text for h in hyps}
    assert texts == {"idea-a", "idea-b", "idea-c"}
    # default step prompt + roster threaded through
    assert captured["topic"] == "topic"
    assert "Topic: topic" in captured["step_prompt"]
    assert captured["roster"]  # non-empty default roster


def test_generate_orders_novel_first_then_by_aesthetic():
    gather, _ = _gather_returning([
        _Resp("r", "1. restated-one\n2. novel-low\n3. novel-high"),
    ])
    verdicts = {
        "restated-one": "restated",
        "novel-low": "novel",
        "novel-high": "novel",
    }
    scores = {"restated-one": 0.99, "novel-low": 0.2, "novel-high": 0.8}
    hyps = H.generate_hypotheses(
        "t", gather=gather,
        assess=lambda t: _Wrap(verdicts[t]),
        score_fn=lambda t: scores[t],
    )
    # novel ideas first (despite restated having the highest aesthetic),
    # and among novel ones higher aesthetic wins.
    assert [h.text for h in hyps] == ["novel-high", "novel-low", "restated-one"]


def test_generate_dedups_identical_ideas_case_insensitive():
    gather, _ = _gather_returning([
        _Resp("a", "1. Same Idea"),
        _Resp("b", "1. same idea"),
    ])
    hyps = H.generate_hypotheses(
        "t", gather=gather, assess=lambda t: _Wrap("novel"), score_fn=lambda t: 0.5,
    )
    assert len(hyps) == 1


def test_generate_skips_errored_agents():
    gather, _ = _gather_returning([
        _Resp("a", "", error="timeout"),
        _Resp("b", "1. good idea"),
    ])
    hyps = H.generate_hypotheses(
        "t", gather=gather, assess=lambda t: _Wrap("novel"), score_fn=lambda t: None,
    )
    assert [h.text for h in hyps] == ["good idea"]


def test_generate_truncates_to_n():
    gather, _ = _gather_returning([
        _Resp("a", "\n".join(f"{i}. idea-{i}" for i in range(1, 11))),
    ])
    hyps = H.generate_hypotheses(
        "t", n=3, gather=gather, assess=lambda t: _Wrap("novel"), score_fn=lambda t: 0.5,
    )
    assert len(hyps) == 3


def test_generate_failure_isolated_when_gather_raises():
    def boom(**kwargs):
        raise RuntimeError("llm down")

    assert H.generate_hypotheses("t", gather=boom) == []


def test_generate_novelty_failure_defaults_to_novel():
    gather, _ = _gather_returning([_Resp("a", "1. idea")])

    def bad_assess(t):
        raise ValueError("kb down")

    hyps = H.generate_hypotheses("t", gather=gather, assess=bad_assess, score_fn=lambda t: None)
    assert len(hyps) == 1
    assert hyps[0].novelty == "novel"


def test_hypothesis_to_dict():
    h = Hypothesis(text="x", role="coder", novelty="recombination",
                   aesthetic=0.3, notes=["n1"])
    assert h.to_dict() == {
        "text": "x", "role": "coder", "novelty": "recombination",
        "aesthetic": 0.3, "notes": ["n1"],
    }
