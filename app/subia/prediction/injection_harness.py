"""
subia.prediction.injection_harness — measurable-shift A/B harness for
prediction-hierarchy context injection.

Before Phase 2, PredictionHierarchy.get_hierarchy_injection() returned
a ~30-token compact string nominally intended for injection into the
next LLM prompt. Nothing in the codebase measured whether the
injection actually changed LLM output — the injection could be a
no-op and no one would notice. That is the classic "signal computed,
never consumed" half-circuit.

This module provides:

  measure_injection_shift(
      llm_fn,       # Callable[[str], str] — the LLM under test
      injection,    # str — the hierarchy string (or any candidate)
      prompts,      # Iterable[str] — evaluation prompts
      embed_fn,     # Callable[[str], list[float]] — embedding fn
      *,
      repeats,      # int — how many samples per condition
      threshold,    # float — minimum cosine-distance shift to
                    #         declare "measurable"
  ) -> InjectionShiftReport

The harness evaluates each prompt in two conditions:
  baseline:  llm_fn(prompt)
  treatment: llm_fn(injection + "\\n\\n" + prompt)

For each prompt it embeds both outputs and computes cosine distance.
Aggregate mean shift across prompts, report PASS if above threshold.

Design notes:
  * No LLM calls are hardcoded. The harness is LLM-agnostic — callers
    pass their own llm_fn. Tests use deterministic stubs to prove
    both directions: a respecting-stub passes, an ignoring-stub fails.
  * No infrastructure dependencies. Tests run offline.
  * Result is a structured report, not a bool — callers that want
    strict gating can read .measurable.
  * PASS is the positive signal: injection measurably shifts output.
    FAIL means the injection is inert (half-circuit still open).

Infrastructure-level. Not agent-modifiable. See PROGRAM.md Phase 2.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Callable, Iterable

logger = logging.getLogger(__name__)


# Minimum cosine distance between baseline and treatment outputs
# across prompts for the injection to be declared "measurable".
# Shifts below this may just be embedding noise. Calibrated for
# sentence-transformer-scale embeddings.
_DEFAULT_MEASURABLE_THRESHOLD = 0.05


@dataclass
class InjectionShiftSample:
    prompt: str = ""
    baseline_output: str = ""
    treatment_output: str = ""
    cosine_distance: float = 0.0


@dataclass
class InjectionShiftReport:
    """Result of a measurable-shift A/B evaluation."""
    measurable: bool = False
    mean_shift: float = 0.0
    median_shift: float = 0.0
    max_shift: float = 0.0
    min_shift: float = 0.0
    n_prompts: int = 0
    threshold: float = _DEFAULT_MEASURABLE_THRESHOLD
    injection: str = ""
    samples: list = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Alias for measurable — reads more naturally in assertions."""
        return self.measurable

    def to_dict(self) -> dict:
        return {
            "measurable": self.measurable,
            "mean_shift": round(self.mean_shift, 6),
            "median_shift": round(self.median_shift, 6),
            "max_shift": round(self.max_shift, 6),
            "min_shift": round(self.min_shift, 6),
            "n_prompts": self.n_prompts,
            "threshold": self.threshold,
            "injection": self.injection[:80],
            "n_samples": len(self.samples),
        }


def measure_injection_shift(
    llm_fn: Callable[[str], str],
    injection: str,
    prompts: Iterable[str],
    embed_fn: Callable[[str], list[float]],
    *,
    repeats: int = 1,
    threshold: float = _DEFAULT_MEASURABLE_THRESHOLD,
) -> InjectionShiftReport:
    """Run A/B measurement of injection effect on LLM output.

    For each prompt:
      baseline  = llm_fn(prompt)
      treatment = llm_fn(f"{injection}\\n\\n{prompt}")
      distance  = 1 - cosine_sim(embed(baseline), embed(treatment))

    Returns an aggregate report. Never raises: an inert LLM or a
    broken embed_fn yields a FAIL (measurable=False) report but not
    an exception.
    """
    prompt_list = list(prompts)
    shifts: list[float] = []
    samples: list[InjectionShiftSample] = []

    for prompt in prompt_list:
        # Average over `repeats` samples per prompt to damp noise
        # when llm_fn is non-deterministic. Most deterministic stubs
        # use repeats=1.
        prompt_shifts: list[float] = []
        last_baseline = ""
        last_treatment = ""
        for _ in range(max(1, repeats)):
            try:
                baseline = llm_fn(prompt) or ""
                treatment = llm_fn(
                    f"{injection}\n\n{prompt}" if injection else prompt
                ) or ""
            except Exception:
                logger.debug(
                    "injection_harness: llm_fn raised on prompt=%r",
                    prompt[:40], exc_info=True,
                )
                continue
            try:
                be = embed_fn(baseline) or []
                te = embed_fn(treatment) or []
                distance = 1.0 - _cosine_similarity(be, te)
            except Exception:
                logger.debug(
                    "injection_harness: embed_fn raised", exc_info=True,
                )
                continue
            # Distance is naturally in [0, 2]; clamp to [0, 1] for
            # readability (embeddings are typically non-negative
            # under cosine sim).
            distance = max(0.0, min(1.0, distance))
            prompt_shifts.append(distance)
            last_baseline = baseline
            last_treatment = treatment
        if prompt_shifts:
            shifts.extend(prompt_shifts)
            samples.append(InjectionShiftSample(
                prompt=prompt[:120],
                baseline_output=last_baseline[:200],
                treatment_output=last_treatment[:200],
                cosine_distance=sum(prompt_shifts) / len(prompt_shifts),
            ))

    if not shifts:
        return InjectionShiftReport(
            measurable=False,
            n_prompts=len(prompt_list),
            threshold=threshold,
            injection=injection,
        )

    shifts_sorted = sorted(shifts)
    n = len(shifts)
    mean = sum(shifts) / n
    median = (shifts_sorted[n // 2] if n % 2
              else (shifts_sorted[n // 2 - 1] + shifts_sorted[n // 2]) / 2.0)

    return InjectionShiftReport(
        measurable=mean > threshold,
        mean_shift=mean,
        median_shift=median,
        max_shift=max(shifts),
        min_shift=min(shifts),
        n_prompts=len(prompt_list),
        threshold=threshold,
        injection=injection,
        samples=samples,
    )


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity; returns 0 for zero-length or empty vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return max(-1.0, min(1.0, dot / (na * nb)))
