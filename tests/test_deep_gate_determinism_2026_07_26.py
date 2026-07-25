"""Whether grounding checks run must not depend on the router's difficulty guess.

Measured defect (reports/GATE_DIAGNOSIS_2026-07-25.md + this session):

* `assess_deep_research` mixed text signals with the router's `difficulty`
  integer and compared the sum to a threshold of 4.
* A bare "make me a report on X" scored 2, so it needed difficulty >= 8 to reach
  the gated `deep_research` path.
* `difficulty` is not reproducible: `control_plane.tickets` holds seven runs of
  one byte-identical 183-char report request scored 5, 7 and 8, landing on
  `research` on some runs and `deep_research` on others.
* `app/crews/research_crew.py` has no evidence or citation checking at all, so
  the flip decided whether the answer was grounded.

Four of twelve golden-set questions were flippable on difficulty alone before
the fix. These tests fail on that code.
"""

from __future__ import annotations

import json
import pathlib

import pytest

deep_path = pytest.importorskip("app.research.deep_path")

assess_deep_research = deep_path.assess_deep_research
requires_grounded_synthesis = deep_path.requires_grounded_synthesis

GOLDEN = pathlib.Path(__file__).resolve().parents[1] / "evals" / "golden_set.jsonl"

# The exact text behind the seven production ticket rows.
FOREST_REQUEST = (
    "please make me a report on estona forest health and deforestation data over "
    "the years. research forestry industry business and practices and evaluate "
    "those from critical point in view"
)

# Requests that must stay on the fast path: forcing these onto a ~758s deep run
# would be the opposite failure. Measured latencies: research 104s vs
# deep_research 758s average for report-class tickets.
FAST_PATH_REQUESTS = (
    "what is Estonia's current population?",
    "write a short poem about a Finnish summer evening by a lake",
    "hey, how's it going?",
    "what's on my calendar tomorrow?",
    "write a Python function that computes the Fibonacci sequence up to n terms",
)

DIFFICULTIES = tuple(range(1, 11))


def _golden_rows() -> list[dict]:
    if not GOLDEN.exists():  # pragma: no cover - fixture is in-repo
        pytest.skip(f"golden set not mounted at {GOLDEN}")
    return [
        json.loads(line)
        for line in GOLDEN.read_text().splitlines()
        if line.strip()
    ]


def _prompt(row: dict) -> str:
    return row.get("prompt") or row.get("question") or ""


# ── The core invariant ───────────────────────────────────────────────────────

def test_forest_request_is_gated_at_every_observed_difficulty():
    """The production incident, pinned: 5, 7 and 8 were all observed for this text."""
    verdicts = {
        d: assess_deep_research(FOREST_REQUEST, difficulty=d).use_deep
        for d in (5, 7, 8)
    }
    assert set(verdicts.values()) == {True}, verdicts


def test_every_golden_question_verdict_is_difficulty_invariant():
    """No golden question may change fork because the router guessed differently."""
    flippable = {}
    for row in _golden_rows():
        prompt = _prompt(row)
        verdicts = {
            d: assess_deep_research(prompt, difficulty=d).use_deep
            for d in DIFFICULTIES
        }
        if len(set(verdicts.values())) > 1:
            flippable[row.get("id")] = verdicts
    assert not flippable, (
        "these questions still get grounding checks by coin flip: "
        f"{sorted(flippable)}"
    )


def test_report_shape_alone_clears_the_gate_at_lowest_difficulty():
    """A plain report request must not need a lucky difficulty guess."""
    a = assess_deep_research("report on Tallinn's housing prices", difficulty=1)
    assert a.use_deep
    assert a.grounding_shape == "explicit report request"


def test_analytical_comparison_is_gated_without_difficulty_help():
    a = assess_deep_research(
        "compare the economic and environmental trade-offs of Estonia's oil "
        "shale industry versus renewable energy",
        difficulty=1,
    )
    assert a.use_deep
    assert a.grounding_shape == "analytical comparison across multiple subjects"


# ── The cost side: no over-promotion ────────────────────────────────────────

@pytest.mark.parametrize("request_text", FAST_PATH_REQUESTS)
def test_fast_path_requests_never_promote(request_text):
    """Lookups, chat, poems and code must not be forced onto the deep path."""
    for d in DIFFICULTIES:
        a = assess_deep_research(request_text, difficulty=d)
        assert not a.use_deep, f"{request_text!r} promoted at difficulty={d}"
        assert a.grounding_shape is None


@pytest.mark.parametrize("request_text", FAST_PATH_REQUESTS)
def test_fast_path_requests_require_no_grounding_shape(request_text):
    assert requires_grounded_synthesis(request_text) is None


def test_requires_grounded_synthesis_handles_empty_input():
    assert requires_grounded_synthesis("") is None
    assert requires_grounded_synthesis(None) is None


# ── The residual, kept observable ───────────────────────────────────────────

# No shape match; scores 3 on text alone (sources + evaluate + length), so
# difficulty >= 7 is the deciding vote. This is the residual the shape floor
# does not cover, and it must be reported rather than hidden.
_DIFFICULTY_DECIDED = (
    "please evaluate the primary sources behind this claim and tell me whether "
    "the numbers hold up, because I need to be confident before I quote them in "
    "tomorrow morning's meeting with the board"
)


def test_difficulty_decided_verdict_is_flagged_not_deterministic():
    low = assess_deep_research(_DIFFICULTY_DECIDED, difficulty=1)
    high = assess_deep_research(_DIFFICULTY_DECIDED, difficulty=9)
    assert low.grounding_shape is None and high.grounding_shape is None
    assert low.use_deep != high.use_deep, (
        "fixture no longer exercises the residual; pick another text"
    )
    # Both ends must report the text as flip-prone, not just the end where the
    # bonus happened to tip it — the flag describes the text, not one guess.
    assert high.deterministic is False
    assert low.deterministic is False


def test_shape_gated_verdicts_are_marked_deterministic():
    for d in DIFFICULTIES:
        a = assess_deep_research(FOREST_REQUEST, difficulty=d)
        assert a.deterministic is True, f"difficulty={d} reported as decisive"


def test_clearly_shallow_verdicts_are_marked_deterministic():
    for d in DIFFICULTIES:
        a = assess_deep_research("hey, how's it going?", difficulty=d)
        assert a.deterministic is True


# ── Contract preservation ───────────────────────────────────────────────────

def test_existing_difficulty_reason_strings_are_unchanged():
    """Downstream telemetry reads these strings; they must not drift."""
    seen = {
        d: assess_deep_research("plain question", difficulty=d).reasons
        for d in (6, 7, 8, 9, 10)
    }
    assert "difficulty>=7" in seen[7]
    assert "difficulty>=8" in seen[8]
    assert "difficulty>=9" in seen[9]
    assert "difficulty>=9" in seen[10]
    assert not any(r.startswith("difficulty") for r in seen[6])


def test_score_route_still_promotes_without_a_shape_match():
    """The shape floor is additive — it must not replace the score route."""
    a = assess_deep_research(_DIFFICULTY_DECIDED, difficulty=9)
    assert a.grounding_shape is None
    assert a.use_deep, "score route regressed"


@pytest.fixture
def _auto_promotion_on(monkeypatch):
    """`promote_research_decisions` reads runtime_settings, which needs gateway
    env; without it the function fails closed and promotes nothing. Inject the
    switch so the promotion path itself is what gets tested."""
    import sys
    import types

    stub = types.ModuleType("app.runtime_settings")
    stub.get_deep_research_auto_enabled = lambda: True
    stub.get_deep_research_min_score = lambda: 4
    monkeypatch.setitem(sys.modules, "app.runtime_settings", stub)
    return stub


def test_promotion_records_the_new_fields(_auto_promotion_on):
    decisions = [{"crew": "research", "task": "x", "difficulty": 5}]
    out = deep_path.promote_research_decisions(
        decisions, user_input=FOREST_REQUEST,
    )
    assert out[0]["crew"] == "deep_research"
    assessment = out[0]["deep_research_assessment"]
    assert assessment["grounding_shape"] == "explicit report request"
    assert assessment["deterministic"] is True


def test_promotion_promotes_previously_flippable_report_at_low_difficulty(
    _auto_promotion_on,
):
    """The end-to-end shape of the production incident."""
    decisions = [{"crew": "research", "task": "x", "difficulty": 5}]
    out = deep_path.promote_research_decisions(
        decisions, user_input="write me a critical report on the Estonian dairy "
        "industry's business practices over the last decade, with sources",
    )
    assert out[0]["crew"] == "deep_research"


def test_promotion_leaves_non_research_crews_alone(_auto_promotion_on):
    decisions = [{"crew": "coding", "task": "x", "difficulty": 9}]
    out = deep_path.promote_research_decisions(
        decisions, user_input=FOREST_REQUEST,
    )
    assert out[0]["crew"] == "coding"


def test_promotion_leaves_fast_path_requests_on_the_fast_path(_auto_promotion_on):
    decisions = [{"crew": "research", "task": "x", "difficulty": 9}]
    out = deep_path.promote_research_decisions(
        decisions, user_input="what is Estonia's current population?",
    )
    assert out[0]["crew"] == "research"
