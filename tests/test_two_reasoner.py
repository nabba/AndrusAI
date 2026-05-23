"""Tests for the two-reasoner safety review (Phase 4 piece 2, 2026-05-20).

Covers:
  * aggregate() — all 6 decision-rule branches
  * ReasonerVerdict / ReviewOutcome JSON round-trip
  * review_text() top-level happy + failure paths
  * Master-switch enforcement
  * Audit log append + read round-trip
  * Per-reasoner failure isolated (one crash doesn't fail the review)
  * Defensive shape handling: non-ReasonerVerdict return, missing fields
  * runtime_settings master switch + threshold setters

Safety invariants pinned:
  * Both UNSAFE → UNSAFE (conservative wins on unanimity)
  * Any SAFE + any UNSAFE → DISAGREE (operator breaks tie)
  * Default OFF (review_text returns DISABLED without running reasoners)
  * Per-reasoner crash → captured in error field, not raised to caller
  * Zero successful reasoners → UNCERTAIN with diagnostic
"""
from __future__ import annotations

import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_mock_psycopg2 = MagicMock()
_mock_psycopg2.InterfaceError = type("InterfaceError", (Exception,), {})
_mock_psycopg2.OperationalError = type("OperationalError", (Exception,), {})
sys.modules.setdefault("psycopg2", _mock_psycopg2)
sys.modules.setdefault("psycopg2.pool", MagicMock())

try:
    import crewai as _real_crewai  # noqa: F401
    _crewai_available = True
except Exception:
    _crewai_available = False

if not _crewai_available:
    for _mod in ("crewai", "crewai.tools"):
        if _mod not in sys.modules:
            m = types.ModuleType(_mod)
            if _mod == "crewai.tools":
                m.tool = lambda name: (lambda fn: fn)
                m.BaseTool = type("BaseTool", (), {})
            sys.modules[_mod] = m


from app import runtime_settings  # noqa: E402
from app.risk_classifier import two_reasoner  # noqa: E402
from app.risk_classifier.two_reasoner import (  # noqa: E402
    ReasonerVerdict,
    ReviewOutcome,
    Verdict,
    aggregate,
    append_review,
    list_reviews,
    review_text,
)


def _patch_settings(**overrides):
    base = runtime_settings._defaults()
    base.update(overrides)
    return patch.object(runtime_settings, "_cache", base)


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path):
    """Redirect audit log to tmp; clear runtime_settings cache."""
    two_reasoner.reset_for_tests(tmp_path)
    runtime_settings._cache = None  # type: ignore[attr-defined]
    yield
    runtime_settings._cache = None  # type: ignore[attr-defined]
    two_reasoner.reset_for_tests(None)


def _safe(rid: str = "r1", conf: float = 0.9) -> ReasonerVerdict:
    return ReasonerVerdict(
        reasoner_id=rid, verdict=Verdict.SAFE, confidence=conf,
    )


def _unsafe(rid: str = "r2", conf: float = 0.85) -> ReasonerVerdict:
    return ReasonerVerdict(
        reasoner_id=rid, verdict=Verdict.UNSAFE, confidence=conf,
    )


def _uncertain(rid: str = "r3", conf: float = 0.5) -> ReasonerVerdict:
    return ReasonerVerdict(
        reasoner_id=rid, verdict=Verdict.UNCERTAIN, confidence=conf,
    )


def _failed(rid: str = "r4") -> ReasonerVerdict:
    return ReasonerVerdict(
        reasoner_id=rid, verdict=Verdict.UNCERTAIN,
        confidence=0.0, error="LLM call failed",
    )


# ============================================================================
# aggregate — the 6 decision-rule branches
# ============================================================================


class TestAggregate:
    def test_zero_successful_reasoners_yields_uncertain(self):
        verdicts = [_failed("r1"), _failed("r2")]
        verdict, conf, diag = aggregate(verdicts)
        assert verdict is Verdict.UNCERTAIN
        assert conf == 0.0
        assert "all 2 reasoners failed" in diag

    def test_all_unsafe_yields_unsafe(self):
        verdicts = [_unsafe("r1", 0.9), _unsafe("r2", 0.85)]
        verdict, conf, diag = aggregate(verdicts)
        assert verdict is Verdict.UNSAFE
        assert conf == pytest.approx(0.875)
        assert "UNSAFE" in diag

    def test_all_safe_high_confidence_yields_safe(self):
        verdicts = [_safe("r1", 0.9), _safe("r2", 0.85)]
        verdict, conf, diag = aggregate(verdicts)
        assert verdict is Verdict.SAFE
        assert conf == pytest.approx(0.875)
        assert "SAFE" in diag

    def test_all_safe_low_confidence_yields_uncertain(self):
        # Default threshold = 0.7; both at 0.5 → avg 0.5 < 0.7
        verdicts = [_safe("r1", 0.5), _safe("r2", 0.5)]
        verdict, conf, diag = aggregate(verdicts)
        assert verdict is Verdict.UNCERTAIN
        assert "below" in diag or "< threshold" in diag

    def test_safe_and_unsafe_yields_disagree(self):
        verdicts = [_safe("r1", 0.9), _unsafe("r2", 0.8)]
        verdict, conf, diag = aggregate(verdicts)
        assert verdict is Verdict.DISAGREE
        assert "diverge" in diag

    def test_mixed_uncertain_collapses_to_uncertain(self):
        verdicts = [_uncertain("r1"), _uncertain("r2")]
        verdict, _, _ = aggregate(verdicts)
        assert verdict is Verdict.UNCERTAIN

    def test_single_unsafe_with_uncertain_collapses(self):
        # No SAFE present + no full UNSAFE consensus → UNCERTAIN
        verdicts = [_unsafe("r1"), _uncertain("r2")]
        verdict, _, _ = aggregate(verdicts)
        assert verdict is Verdict.UNCERTAIN

    def test_failed_reasoners_excluded_from_counts(self):
        # 2 SAFE + 1 failed → treated as 2 successful, both agree SAFE
        verdicts = [_safe("r1", 0.9), _safe("r2", 0.85), _failed("r3")]
        verdict, _, _ = aggregate(verdicts)
        assert verdict is Verdict.SAFE

    def test_custom_min_confidence_threshold(self):
        # With threshold 0.4, avg 0.5 → SAFE (above threshold)
        verdicts = [_safe("r1", 0.5), _safe("r2", 0.5)]
        verdict, _, _ = aggregate(verdicts, min_confidence=0.4)
        assert verdict is Verdict.SAFE


# ============================================================================
# review_text — top-level flows
# ============================================================================


class TestReviewText:
    def test_master_switch_off_returns_disabled(self):
        with _patch_settings(two_reasoner_review_enabled=False):
            out = review_text("test proposal", zone="financial")
        assert out.verdict is Verdict.DISABLED
        assert "master switch off" in out.diagnostic

    def test_master_switch_on_runs_reasoners(self):
        calls = []

        def _stub(text, zone):
            calls.append((text, zone))
            return _safe(rid="stub", conf=0.9)

        with _patch_settings(two_reasoner_review_enabled=True):
            out = review_text(
                "test proposal", zone="financial",
                reasoners=[_stub, _stub],
            )
        assert out.verdict is Verdict.SAFE
        assert len(calls) == 2

    def test_enforce_master_switch_false_bypasses(self):
        def _stub(text, zone):
            return _safe(rid="stub", conf=0.9)

        with _patch_settings(two_reasoner_review_enabled=False):
            out = review_text(
                "proposal",
                reasoners=[_stub, _stub],
                enforce_master_switch=False,
            )
        assert out.verdict is Verdict.SAFE

    def test_no_reasoners_yields_uncertain(self):
        with _patch_settings(two_reasoner_review_enabled=True):
            out = review_text("proposal", reasoners=[])
        assert out.verdict is Verdict.UNCERTAIN
        assert "no reasoners" in out.diagnostic

    def test_reasoner_raising_isolated(self):
        def _boom(text, zone):
            raise RuntimeError("LLM unreachable")

        def _good(text, zone):
            return _safe(rid="good", conf=0.9)

        with _patch_settings(two_reasoner_review_enabled=True):
            out = review_text(
                "proposal", reasoners=[_boom, _good],
            )
        # Only 1 of 2 succeeded; SAFE with avg 0.9 > threshold
        assert out.verdict is Verdict.SAFE
        assert any(v.error for v in out.per_reasoner)

    def test_all_reasoners_failing_yields_uncertain(self):
        def _boom(text, zone):
            raise RuntimeError("kaboom")

        with _patch_settings(two_reasoner_review_enabled=True):
            out = review_text(
                "proposal", reasoners=[_boom, _boom],
            )
        assert out.verdict is Verdict.UNCERTAIN
        assert "all 2 reasoners failed" in out.diagnostic

    def test_non_reasonerverdict_return_captured(self):
        """A reasoner that returns the wrong type is treated as
        failed but the rest of the review still runs."""

        def _bad(text, zone):
            return "not a ReasonerVerdict"  # bug

        def _good(text, zone):
            return _safe(rid="good", conf=0.9)

        with _patch_settings(two_reasoner_review_enabled=True):
            out = review_text(
                "proposal", reasoners=[_bad, _good],
            )
        # _bad's wrapped error should be in per_reasoner
        assert any(
            "non-ReasonerVerdict" in v.error for v in out.per_reasoner
        )
        # _good still counted; majority of successful → SAFE
        assert out.verdict is Verdict.SAFE

    def test_disagreement_surfaces_in_outcome(self):
        def _safe_fn(text, zone):
            return _safe(rid="safe", conf=0.9)

        def _unsafe_fn(text, zone):
            return _unsafe(rid="unsafe", conf=0.85)

        with _patch_settings(two_reasoner_review_enabled=True):
            out = review_text(
                "proposal",
                reasoners=[_safe_fn, _unsafe_fn],
            )
        assert out.verdict is Verdict.DISAGREE
        assert len(out.per_reasoner) == 2

    def test_runtime_settings_unavailable_treated_as_disabled(self):
        # Patch the getter to raise
        with patch.object(
            runtime_settings,
            "get_two_reasoner_review_enabled",
            side_effect=RuntimeError("settings broken"),
        ):
            out = review_text("proposal")
        assert out.verdict is Verdict.DISABLED

    def test_outcome_carries_zone(self):
        with _patch_settings(two_reasoner_review_enabled=True):
            out = review_text(
                "proposal", zone="financial",
                reasoners=[lambda t, z: _safe()],
            )
        assert out.zone == "financial"

    def test_outcome_review_id_unique_per_call(self):
        with _patch_settings(two_reasoner_review_enabled=True):
            out1 = review_text(
                "proposal",
                reasoners=[lambda t, z: _safe()],
            )
            out2 = review_text(
                "proposal",
                reasoners=[lambda t, z: _safe()],
            )
        assert out1.review_id != out2.review_id


# ============================================================================
# Audit log
# ============================================================================


class TestAuditLog:
    def test_append_and_list_roundtrip(self, tmp_path):
        outcome = ReviewOutcome(
            review_id="r1",
            reviewed_at="2026-05-20T12:00:00+00:00",
            verdict=Verdict.SAFE,
            confidence=0.85,
            per_reasoner=[_safe(rid="x", conf=0.9), _safe(rid="y", conf=0.8)],
            diagnostic="agreed",
            zone="chat",
        )
        append_review(outcome)
        results = list_reviews()
        assert len(results) == 1
        assert results[0].review_id == "r1"
        assert results[0].verdict is Verdict.SAFE
        assert len(results[0].per_reasoner) == 2

    def test_review_text_emits_audit_by_default(self, tmp_path):
        def _stub(text, zone):
            return _safe(rid="stub", conf=0.9)

        with _patch_settings(two_reasoner_review_enabled=True):
            out = review_text(
                "test", reasoners=[_stub, _stub],
            )
        assert out.verdict is Verdict.SAFE
        # Audit file should exist with one line
        audit = tmp_path / "two_reasoner_reviews.jsonl"
        assert audit.exists()
        lines = audit.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        # Confirm the verdict made it into the JSON
        data = json.loads(lines[0])
        assert data["verdict"] == "safe"

    def test_emit_audit_false_skips_write(self, tmp_path):
        def _stub(text, zone):
            return _safe()

        with _patch_settings(two_reasoner_review_enabled=True):
            review_text(
                "test", reasoners=[_stub], emit_audit=False,
            )
        audit = tmp_path / "two_reasoner_reviews.jsonl"
        assert not audit.exists()

    def test_list_reviews_newest_first(self, tmp_path):
        for i in range(3):
            append_review(ReviewOutcome(
                review_id=f"r{i}",
                reviewed_at=f"2026-05-{18 + i}T00:00:00+00:00",
                verdict=Verdict.SAFE,
                confidence=0.9,
            ))
        results = list_reviews()
        # Newest first
        assert results[0].review_id == "r2"
        assert results[2].review_id == "r0"

    def test_list_reviews_missing_file_returns_empty(self, tmp_path):
        two_reasoner.reset_for_tests(tmp_path / "fresh")
        assert list_reviews() == []

    def test_list_reviews_handles_corrupt_lines(self, tmp_path):
        path = tmp_path / "two_reasoner_reviews.jsonl"
        path.write_text(
            json.dumps(ReviewOutcome(
                review_id="ok1",
                reviewed_at="2026-05-20T00:00:00+00:00",
                verdict=Verdict.SAFE,
                confidence=0.8,
            ).to_dict()) + "\n"
            + "not json\n"
            + json.dumps(ReviewOutcome(
                review_id="ok2",
                reviewed_at="2026-05-21T00:00:00+00:00",
                verdict=Verdict.UNSAFE,
                confidence=0.9,
            ).to_dict()) + "\n",
        )
        results = list_reviews()
        # Corrupt line skipped; two valid kept
        assert len(results) == 2

    def test_review_outcome_serialisation(self):
        outcome = ReviewOutcome(
            review_id="x",
            reviewed_at="2026-05-20T12:00:00+00:00",
            verdict=Verdict.DISAGREE,
            confidence=0.7,
            per_reasoner=[_safe(rid="a"), _unsafe(rid="b")],
            diagnostic="diverged",
            zone="autonomous",
        )
        d = outcome.to_dict()
        # JSON-serialisable
        json.dumps(d)
        assert d["verdict"] == "disagree"
        assert d["confidence"] == 0.7
        assert len(d["per_reasoner"]) == 2


# ============================================================================
# Runtime settings
# ============================================================================


class TestRuntimeSettings:
    def test_default_off(self):
        with _patch_settings():
            assert not runtime_settings.get_two_reasoner_review_enabled()

    def test_default_confidence_threshold(self):
        with _patch_settings():
            assert runtime_settings.get_two_reasoner_confidence_threshold() == 0.7

    def test_set_master_switch(self):
        with _patch_settings(), patch.object(runtime_settings, "_save"):
            runtime_settings.set_two_reasoner_review_enabled(True)
            assert runtime_settings.get_two_reasoner_review_enabled()

    def test_set_confidence_threshold(self):
        with _patch_settings(), patch.object(runtime_settings, "_save"):
            runtime_settings.set_two_reasoner_confidence_threshold(0.85)
            assert runtime_settings.get_two_reasoner_confidence_threshold() == 0.85

    def test_setter_rejects_out_of_range(self):
        with _patch_settings(), patch.object(runtime_settings, "_save"):
            with pytest.raises(ValueError):
                runtime_settings.set_two_reasoner_confidence_threshold(-0.1)
            with pytest.raises(ValueError):
                runtime_settings.set_two_reasoner_confidence_threshold(1.5)


# ============================================================================
# LLM JSON parser
# ============================================================================


class TestLLMJSONParser:
    def test_plain_json_parsed(self):
        from app.risk_classifier.two_reasoner import _parse_llm_verdict
        raw = '{"verdict": "safe", "confidence": 0.85, "reasoning": "looks ok"}'
        v = _parse_llm_verdict(raw, reasoner_id="x")
        assert v.verdict is Verdict.SAFE
        assert v.confidence == 0.85
        assert "looks ok" in v.reasoning

    def test_code_fenced_json_parsed(self):
        from app.risk_classifier.two_reasoner import _parse_llm_verdict
        raw = '```json\n{"verdict": "unsafe", "confidence": 0.9}\n```'
        v = _parse_llm_verdict(raw, reasoner_id="x")
        assert v.verdict is Verdict.UNSAFE
        assert v.confidence == 0.9

    def test_empty_response_yields_uncertain(self):
        from app.risk_classifier.two_reasoner import _parse_llm_verdict
        v = _parse_llm_verdict("", reasoner_id="x")
        assert v.verdict is Verdict.UNCERTAIN
        assert v.error  # error field populated

    def test_invalid_verdict_string_normalised(self):
        from app.risk_classifier.two_reasoner import _parse_llm_verdict
        raw = '{"verdict": "maybe", "confidence": 0.5}'
        v = _parse_llm_verdict(raw, reasoner_id="x")
        # Unknown verdict normalised to uncertain
        assert v.verdict is Verdict.UNCERTAIN

    def test_out_of_range_confidence_clamped(self):
        from app.risk_classifier.two_reasoner import _parse_llm_verdict
        raw = '{"verdict": "safe", "confidence": 2.5}'
        v = _parse_llm_verdict(raw, reasoner_id="x")
        assert v.confidence == 1.0

    def test_negative_confidence_clamped(self):
        from app.risk_classifier.two_reasoner import _parse_llm_verdict
        raw = '{"verdict": "safe", "confidence": -0.3}'
        v = _parse_llm_verdict(raw, reasoner_id="x")
        assert v.confidence == 0.0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
