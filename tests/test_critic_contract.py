"""Regression tests for the critic delivery contract."""
from __future__ import annotations

from app.crews.critic_crew import _apply_review_result


def test_pass_preserves_original() -> None:
    original = "A complete evidence-grounded answer."
    assert _apply_review_result(original, "PASS") == (original, "pass")


def test_revised_replaces_original_with_complete_answer() -> None:
    revised, outcome = _apply_review_result(
        "The unsupported original answer.",
        "REVISED\nThe corrected answer cites the supplied evidence and removes the claim.",
    )
    assert outcome == "revised"
    assert revised.startswith("The corrected answer")
    assert "unsupported original" not in revised


def test_block_withholds_critical_answer() -> None:
    delivery, outcome = _apply_review_result(
        "The original falsely claims a verified result.",
        "BLOCK\nThe cited source does not support the central factual claim.",
    )
    assert outcome == "blocked"
    assert "withholding" in delivery
    assert "original falsely" not in delivery


def test_legacy_critical_feedback_also_blocks() -> None:
    delivery, outcome = _apply_review_result(
        "Unsafe original.",
        "Critical: the URL is fabricated and cannot be repaired.",
    )
    assert outcome == "blocked"
    assert "fabricated" in delivery


def test_malformed_noncritical_feedback_does_not_replace_answer() -> None:
    original = "A usable original answer."
    assert _apply_review_result(original, "Minor: adjust the title.") == (
        original,
        "malformed",
    )


def test_negated_critical_word_does_not_block_the_answer() -> None:
    original = "A usable original answer."
    assert _apply_review_result(original, "There are no critical issues.") == (
        original,
        "malformed",
    )


def test_pass_with_caveats_is_not_accepted_as_a_pass_contract() -> None:
    original = "A usable original answer."
    assert _apply_review_result(original, "PASS with caveats") == (
        original,
        "malformed",
    )


def test_revised_prefix_cannot_replace_the_answer() -> None:
    original = "A usable original answer."
    assert _apply_review_result(
        original,
        "REVISED-ish content that must not replace the original answer.",
    ) == (original, "malformed")
