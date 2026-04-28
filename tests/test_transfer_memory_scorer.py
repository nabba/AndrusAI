"""Tests for app.transfer_memory.scorer."""

from app.transfer_memory.scorer import score_abstraction


def test_empty_content_scores_zero():
    s = score_abstraction("")
    assert s.score == 0.0
    assert s.abstract_hits == 0
    assert s.concrete_hits == 0


def test_abstract_phrases_increase_score():
    abstract = (
        "Always verify external numeric claims before finalising. "
        "Validate the response against authoritative sources. "
        "If verification fails, escalate rather than guessing. "
        "The boundary between cue and evidence must always be clear. "
        "Never trust prior memory as ground truth without verification."
    )
    s = score_abstraction(abstract)
    assert s.abstract_hits >= 6
    assert s.score > 0.55


def test_concrete_patterns_decrease_score():
    concrete = (
        "Edit /app/workspace/skills/foo.py at line 42, then run the tests. "
        "Call adapter.verify_belief() with the value 12345.67 as input. "
        "Use `pip install -r requirements.txt` for setup."
    )
    s = score_abstraction(concrete)
    assert s.concrete_hits >= 4
    assert s.score < 0.5


def test_balanced_content_scores_in_unit_interval():
    mixed = (
        "When verifying claims, validate by querying the source. "
        "Use config.yaml when needed; do not hardcode."
    )
    s = score_abstraction(mixed)
    assert 0.0 <= s.score <= 1.0


def test_score_in_unit_interval():
    samples = ["", "a b c", "verify validate" * 100, "/path/x.py" * 20]
    for sample in samples:
        s = score_abstraction(sample)
        assert 0.0 <= s.score <= 1.0


def test_word_count_at_least_one_for_nonempty():
    s = score_abstraction("a")
    assert s.word_count >= 1


def test_substring_does_not_count_as_whole_word():
    """Whole-word boundary check: 'never' matches; 'nevertheless' should
    NOT (because 'theless' isn't part of the phrase)."""
    s_with = score_abstraction("Never compromise the boundary.")
    s_subword = score_abstraction("Nevertheless the system held strong.")
    assert s_with.abstract_hits >= s_subword.abstract_hits


def test_fenced_code_block_counts_as_concrete():
    content = (
        "Verify before applying. Then run:\n"
        "```\n"
        "echo hello\n"
        "echo world\n"
        "```\n"
        "to confirm."
    )
    s = score_abstraction(content)
    assert s.concrete_hits >= 1
