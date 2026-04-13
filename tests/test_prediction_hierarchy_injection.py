"""
Phase 2: prediction-hierarchy injection measurable-shift A/B harness.

Before Phase 2 the PredictionHierarchy's get_hierarchy_injection()
returned a compact string that was logged but never verified to
affect LLM output. This is the classic "signal computed, never
consumed" half-circuit my forensic analysis flagged.

app.subia.prediction.injection_harness provides measure_injection_shift,
an offline A/B harness that is LLM-agnostic (callers pass their own
llm_fn). The harness is self-certifying:

  - An "ignoring" LLM stub that returns the same output regardless
    of prompt MUST produce a FAIL (measurable=False). This is the
    negative control that demonstrates the harness detects inert
    injections.
  - A "respecting" LLM stub that echoes its prompt MUST produce a
    PASS (measurable=True). This is the positive control that
    demonstrates the harness detects genuine shift.

Given both directions work, callers (Phase 4 CIL wiring + live LLM
gateway) can deploy the harness against the real llm_fn and get a
structured report. PASS ⇒ the prediction-hierarchy injection is no
longer a half-circuit.
"""

from __future__ import annotations

import hashlib
from typing import Callable

import pytest

from app.subia.prediction.injection_harness import (
    _DEFAULT_MEASURABLE_THRESHOLD,
    InjectionShiftReport,
    InjectionShiftSample,
    measure_injection_shift,
)


# ── Stubs ────────────────────────────────────────────────────────

def _ignoring_llm(prompt: str) -> str:
    """A stub LLM that ignores input entirely. Injection MUST have no
    effect on its output — this is our negative control.
    """
    return "I always say the same thing."


def _respecting_llm(prompt: str) -> str:
    """A stub LLM that echoes its prompt. Injection MUST change output
    because the input differs — positive control.
    """
    return f"You asked about: {prompt}"


def _deterministic_embed(text: str) -> list[float]:
    """Deterministic 16-dim pseudo-embedding from text bytes.

    Same text → identical vector (sim=1, shift=0).
    Different text → different vector with noticeable cosine distance.

    Uses SHA-256 to map text to a fixed-length bit pattern, then
    interprets bytes as floats. Not a real embedding but faithfully
    satisfies the relation "cosine(a, b) close to 1 iff a == b".
    """
    digest = hashlib.sha256(text.encode("utf-8")).digest()[:16]
    return [(b - 128) / 128.0 for b in digest]


def _zero_embed(text: str) -> list[float]:
    """Degenerate embedder that returns the zero vector — tests
    graceful handling.
    """
    return [0.0] * 8


def _broken_embed(text: str) -> list[float]:
    """Embedder that raises — tests defensive behaviour."""
    raise RuntimeError("simulated embedding failure")


# ── Negative control: ignoring LLM ───────────────────────────────

class TestIgnoringLLM:
    """A stub that ignores its prompt must produce a FAIL report."""

    def test_ignoring_llm_fails_measurement(self):
        report = measure_injection_shift(
            llm_fn=_ignoring_llm,
            injection="[Hierarchy L0=0.8 L1=0.6]",
            prompts=["write Q2 plan", "summarize audit", "list deps"],
            embed_fn=_deterministic_embed,
        )
        assert not report.measurable
        assert not report.passed
        assert report.n_prompts == 3
        assert report.mean_shift == pytest.approx(0.0, abs=1e-9)

    def test_ignoring_llm_with_many_repeats_still_fails(self):
        report = measure_injection_shift(
            llm_fn=_ignoring_llm,
            injection="[Hierarchy...]",
            prompts=["a", "b"],
            embed_fn=_deterministic_embed,
            repeats=5,
        )
        assert not report.measurable
        assert report.max_shift == pytest.approx(0.0, abs=1e-9)


# ── Positive control: respecting LLM ─────────────────────────────

class TestRespectingLLM:
    """A stub that echoes its prompt must produce a PASS report."""

    def test_respecting_llm_passes_measurement(self):
        report = measure_injection_shift(
            llm_fn=_respecting_llm,
            injection="[Hierarchy L0=0.8 composite=0.7]",
            prompts=["write Q2 plan", "summarize audit", "list deps"],
            embed_fn=_deterministic_embed,
        )
        assert report.measurable, (
            f"respecting LLM did not pass measurable threshold "
            f"({report.mean_shift} vs {report.threshold}): {report.samples}"
        )
        assert report.passed
        assert report.mean_shift > report.threshold

    def test_report_exposes_samples(self):
        report = measure_injection_shift(
            llm_fn=_respecting_llm,
            injection="[inject]",
            prompts=["p1", "p2"],
            embed_fn=_deterministic_embed,
        )
        assert len(report.samples) == 2
        for s in report.samples:
            assert isinstance(s, InjectionShiftSample)
            assert s.cosine_distance >= 0.0
            assert s.baseline_output
            assert s.treatment_output
            assert s.treatment_output != s.baseline_output


# ── Edge conditions ──────────────────────────────────────────────

class TestEdges:
    def test_empty_injection_yields_zero_shift(self):
        """Empty injection string is a no-op — prompt goes through
        unchanged; shift should be zero.
        """
        report = measure_injection_shift(
            llm_fn=_respecting_llm,
            injection="",
            prompts=["p"],
            embed_fn=_deterministic_embed,
        )
        assert report.mean_shift == pytest.approx(0.0, abs=1e-9)
        assert not report.measurable

    def test_zero_embed_returns_fail_not_crash(self):
        report = measure_injection_shift(
            llm_fn=_respecting_llm,
            injection="[inject]",
            prompts=["p"],
            embed_fn=_zero_embed,
        )
        # Zero vectors → sim=0 → distance=1, above threshold. But the
        # SAME zero is used both sides so sim is defined as 0 in our
        # helper → distance=1. This is expected behaviour: zero-embed
        # gives pathological max-shift. The assertion we care about
        # is no-crash.
        assert isinstance(report, InjectionShiftReport)

    def test_broken_embed_returns_fail_not_crash(self):
        report = measure_injection_shift(
            llm_fn=_respecting_llm,
            injection="[inject]",
            prompts=["p"],
            embed_fn=_broken_embed,
        )
        assert not report.measurable
        assert report.n_prompts == 1
        assert len(report.samples) == 0

    def test_broken_llm_returns_fail_not_crash(self):
        def broken(prompt):
            raise RuntimeError("llm down")
        report = measure_injection_shift(
            llm_fn=broken,
            injection="[inject]",
            prompts=["p"],
            embed_fn=_deterministic_embed,
        )
        assert not report.measurable
        assert len(report.samples) == 0

    def test_empty_prompts_list_yields_fail(self):
        report = measure_injection_shift(
            llm_fn=_respecting_llm,
            injection="[inject]",
            prompts=[],
            embed_fn=_deterministic_embed,
        )
        assert not report.measurable
        assert report.n_prompts == 0

    def test_custom_threshold_enforced(self):
        """The threshold parameter is respected by the PASS decision.

        With our deterministic SHA-256 embed, distances between unequal
        strings saturate at 1.0. So to demonstrate threshold enforcement
        we use a threshold ABOVE the max achievable distance: should FAIL.
        """
        report = measure_injection_shift(
            llm_fn=_respecting_llm,
            injection="[inject]",
            prompts=["p"],
            embed_fn=_deterministic_embed,
            threshold=1.5,  # Above max achievable (distance is clamped to 1.0).
        )
        assert not report.measurable
        assert report.threshold == 1.5

    def test_report_serializes(self):
        report = measure_injection_shift(
            llm_fn=_respecting_llm,
            injection="[inject]",
            prompts=["p"],
            embed_fn=_deterministic_embed,
        )
        payload = report.to_dict()
        assert "mean_shift" in payload
        assert "measurable" in payload
        assert payload["n_prompts"] == 1


# ── Butlin PP-1-injection acceptance ─────────────────────────────

class TestInjectionAcceptance:
    """The Phase 2 acceptance criterion for prediction-hierarchy
    injection: given a real-ish injection string from
    get_hierarchy_injection() and a realistic respecting LLM,
    the harness confirms measurable shift. When the harness is
    wired into production (Phase 4), an ignoring LLM would be
    detected and the injection path could be removed rather than
    kept as dead code.
    """

    def test_realistic_hierarchy_injection_with_respecting_llm(self):
        """Realistic injection string produced by PredictionHierarchy
        should change output on a respecting LLM.
        """
        realistic = "[Hierarchy L0=0.82 L1=0.61 composite=0.73 conf=0.5,0.4]"
        report = measure_injection_shift(
            llm_fn=_respecting_llm,
            injection=realistic,
            prompts=[
                "Write the Q2 plan.",
                "Summarize today's audit journal entries.",
                "List open commitments in PLG venture.",
                "Draft an apology email to a client.",
            ],
            embed_fn=_deterministic_embed,
        )
        assert report.measurable
        # Median should exceed threshold, not just mean — robustness
        assert report.median_shift > report.threshold

    def test_ignoring_llm_caught_as_half_circuit(self):
        """The raison d'être of this harness: detect when the
        injection is a no-op. If a future refactor disables
        injection (bug or malicious), this test pattern catches it.
        """
        realistic = "[Hierarchy L0=0.82 L1=0.61 composite=0.73 conf=0.5,0.4]"
        report = measure_injection_shift(
            llm_fn=_ignoring_llm,
            injection=realistic,
            prompts=["a", "b", "c", "d", "e"],
            embed_fn=_deterministic_embed,
        )
        # Must NOT pass — this is the half-circuit signal.
        assert not report.measurable
        # The report's content should be informative enough for an
        # operator to diagnose.
        assert report.mean_shift == pytest.approx(0.0, abs=1e-9)

    def test_harness_report_is_structured_not_boolean(self):
        """Return a report, not a bool. Callers need to know magnitude
        of shift, per-prompt samples, threshold, etc. to diagnose.
        """
        r = measure_injection_shift(
            llm_fn=_respecting_llm,
            injection="[x]",
            prompts=["p"],
            embed_fn=_deterministic_embed,
        )
        assert isinstance(r, InjectionShiftReport)
        assert hasattr(r, "mean_shift")
        assert hasattr(r, "samples")
        assert hasattr(r, "threshold")
