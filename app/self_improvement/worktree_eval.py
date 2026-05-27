"""worktree_eval — the judgement of whether a verified change is an IMPROVEMENT.

This is the module that kills the failure that started the 2026-05-27 rebuild:
the old engine scored a code mutation `+0.0133` with a signal that never ran the
changed code, and treated that noise as "keep." Here, the verdict is computed
from signals that are produced by actually running the changed code, and a
within-noise delta can never read as an improvement.

╔══════════════════════════════════════════════════════════════════════════╗
║ TIER_IMMUTABLE — JUDGEMENT FUNCTION. DO NOT make this agent-editable.       ║
║ The Critical Safety Invariant: an engine that can rewrite its own evaluator ║
║ can lower its own bar. The verdict math, thresholds, the held-out benchmark ║
║ loader, and the judge prompt live here and must stay in TIER_IMMUTABLE      ║
║ (registered in app/auto_deployer.py). The EXECUTION that produces the raw   ║
║ signals (running tests / tasks against worktrees) lives in the OPEN evolver ║
║ job — execution is what we WANT to be improvable; judgement is not.         ║
╚══════════════════════════════════════════════════════════════════════════╝

Inputs (produced by the OPEN evolver job, consumed here):
  * ``invariants_ok``   — tests green + public API preserved (from the
    verified implementer; correctness proven by execution).
  * ``CorrectnessResult`` — failing-test-id sets on baseline vs candidate over
    the SAME target test set. Deterministic, no LLM, no noise.
  * ``QualityResult``   — paired per-task judge scores on baseline vs candidate,
    run through the REAL entry point. Optional; only where a held-out benchmark
    exists for the target.

Verdict precedence (``compute_verdict``):
  1. invariants failed                        → REJECT
  2. correctness regression (was-passing→fail) → REJECT
  3. quality regression (consistent, ≥ effect) → REJECT
  4. correctness improved (fixed failing tests) OR quality improved → IMPROVED
  5. nothing measurable to compare against     → INVARIANTS_ONLY (operator decides)
  6. measured but no benefit                   → NO_CHANGE (discard)
Only IMPROVED / INVARIANTS_ONLY are ``proposable``.
"""
from __future__ import annotations

import json
import logging
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ── Thresholds (immutable defaults; the bar the Self-Improver cannot lower) ──


@dataclass(frozen=True)
class EvalThresholds:
    """The improvement bar. Frozen + owned by this immutable module."""

    # Candidate mean judge-score must beat baseline by at least this (0-1 scale).
    # 0.05 is comfortably above per-task LLM-judge jitter; a +0.013-style delta
    # is structurally below the bar and cannot read as IMPROVED.
    min_quality_effect: float = 0.05
    # Minimum paired tasks before a quality verdict is trusted at all.
    min_quality_samples: int = 4


DEFAULT_THRESHOLDS = EvalThresholds()


# ── Raw signal containers ────────────────────────────────────────────────────


@dataclass(frozen=True)
class CorrectnessResult:
    """Failing-test-id sets over the SAME target test set, baseline vs candidate.

    ``ran`` is True whenever a target set was actually executed (even if nothing
    changed) — distinguishes "measured, no change" from "not measured."
    """

    baseline_failed: frozenset[str] = frozenset()
    candidate_failed: frozenset[str] = frozenset()
    ran: bool = False

    @property
    def fixes(self) -> frozenset[str]:
        """Tests failing on baseline that pass on the candidate."""
        return self.baseline_failed - self.candidate_failed

    @property
    def regressions(self) -> frozenset[str]:
        """Tests passing on baseline that fail on the candidate — never OK."""
        return self.candidate_failed - self.baseline_failed

    @property
    def delta(self) -> int:
        return len(self.fixes) - len(self.regressions)


@dataclass(frozen=True)
class QualityResult:
    """Paired per-task judge scores (0-1), SAME tasks in SAME order."""

    baseline_scores: tuple[float, ...] = ()
    candidate_scores: tuple[float, ...] = ()

    @property
    def measured(self) -> bool:
        return (
            len(self.candidate_scores) > 0
            and len(self.candidate_scores) == len(self.baseline_scores)
        )

    @property
    def mean_delta(self) -> float:
        if not self.measured:
            return 0.0
        return statistics.fmean(self.candidate_scores) - statistics.fmean(
            self.baseline_scores
        )

    @property
    def wins(self) -> int:
        return sum(1 for b, c in zip(self.baseline_scores, self.candidate_scores) if c > b)

    @property
    def losses(self) -> int:
        return sum(1 for b, c in zip(self.baseline_scores, self.candidate_scores) if c < b)


# ── Verdict ──────────────────────────────────────────────────────────────────

VERDICTS = frozenset(
    {"REJECT", "IMPROVED", "INVARIANTS_ONLY", "NO_CHANGE"}
)


@dataclass
class EvalVerdict:
    verdict: str
    invariants_ok: bool
    correctness_delta: Optional[int] = None
    quality_delta: Optional[float] = None
    reason: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def proposable(self) -> bool:
        """A change worth putting in front of the operator: it either improves a
        measured signal, or it's a correctness change with nothing to measure
        against. A measured no-benefit change (NO_CHANGE) is discarded — that's
        the class the old engine wrongly proposed as borderline."""
        return self.verdict in ("IMPROVED", "INVARIANTS_ONLY")

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "invariants_ok": self.invariants_ok,
            "correctness_delta": self.correctness_delta,
            "quality_delta": self.quality_delta,
            "reason": self.reason,
            "evidence": self.evidence,
            "notes": list(self.notes),
        }


def compute_verdict(
    *,
    invariants_ok: bool,
    correctness: Optional[CorrectnessResult] = None,
    quality: Optional[QualityResult] = None,
    thresholds: EvalThresholds = DEFAULT_THRESHOLDS,
) -> EvalVerdict:
    """Pure verdict logic. No I/O, no LLM, fully deterministic — the immutable
    heart of the engine's judgement."""
    evidence: dict[str, Any] = {}

    # 1. Correctness is non-negotiable: a change whose tests are red or whose
    #    public API broke is never an improvement.
    if not invariants_ok:
        return EvalVerdict(
            "REJECT",
            invariants_ok=False,
            reason="invariants failed (tests red or public API broken)",
            evidence=evidence,
        )

    # 2. A regression in the target test set is an automatic reject, regardless
    #    of any quality gain elsewhere.
    corr_delta: Optional[int] = None
    if correctness is not None and correctness.ran:
        corr_delta = correctness.delta
        evidence["correctness"] = {
            "fixes": sorted(correctness.fixes),
            "regressions": sorted(correctness.regressions),
            "delta": corr_delta,
        }
        if correctness.regressions:
            return EvalVerdict(
                "REJECT",
                invariants_ok=True,
                correctness_delta=corr_delta,
                reason=f"correctness regression: {sorted(correctness.regressions)[:5]}",
                evidence=evidence,
            )

    # 3. Quality signal (optional). Require BOTH a mean effect AND a consistent
    #    direction (more per-task wins than losses) — a within-noise mean delta
    #    with mixed wins/losses does NOT pass. This is the structural block on
    #    the old "+0.0133" false positive.
    q_improved = q_regressed = False
    q_delta: Optional[float] = None
    if quality is not None and quality.measured:
        q_delta = quality.mean_delta
        wins, losses, n = quality.wins, quality.losses, len(quality.candidate_scores)
        evidence["quality"] = {
            "mean_delta": round(q_delta, 4),
            "wins": wins,
            "losses": losses,
            "samples": n,
            "min_effect": thresholds.min_quality_effect,
        }
        if n < thresholds.min_quality_samples:
            evidence["quality"]["note"] = "too few samples to trust — ignored"
        else:
            if q_delta >= thresholds.min_quality_effect and wins > losses:
                q_improved = True
            elif q_delta <= -thresholds.min_quality_effect and losses > wins:
                q_regressed = True

    if q_regressed:
        return EvalVerdict(
            "REJECT",
            invariants_ok=True,
            correctness_delta=corr_delta,
            quality_delta=q_delta,
            reason=f"quality regression (Δ={q_delta:+.4f}, {quality.losses}↓ vs {quality.wins}↑)",
            evidence=evidence,
        )

    # 4. Did anything measurably improve?
    corr_improved = corr_delta is not None and corr_delta > 0
    # "Measured" = there was a real, TRUSTWORTHY opportunity to show improvement:
    # baseline test failures the candidate could fix, OR a held-out quality
    # benchmark with enough samples to trust. A green-stays-green change with
    # neither isn't a no-benefit result — it's an unmeasured correctness change
    # (operator call). An untrustworthy benchmark (too few samples) does NOT
    # count as measured, so it can't push a correct change to NO_CHANGE.
    quality_trusted = (
        quality is not None
        and quality.measured
        and len(quality.candidate_scores) >= thresholds.min_quality_samples
    )
    measured = (
        correctness is not None
        and correctness.ran
        and len(correctness.baseline_failed) > 0
    ) or quality_trusted

    if corr_improved or q_improved:
        bits = []
        if corr_improved:
            bits.append(f"fixed {len(correctness.fixes)} test(s)")
        if q_improved:
            bits.append(f"benchmark Δ={q_delta:+.4f} ({quality.wins}↑/{quality.losses}↓)")
        return EvalVerdict(
            "IMPROVED",
            invariants_ok=True,
            correctness_delta=corr_delta,
            quality_delta=q_delta,
            reason="; ".join(bits),
            evidence=evidence,
        )

    if not measured:
        # Correctness proven by invariants, but there was nothing to compare
        # against (no failing test fixed, no benchmark for this target). The
        # operator gate decides — this is the legitimate home for bug-fixes and
        # refactors whose value isn't captured by a benchmark.
        return EvalVerdict(
            "INVARIANTS_ONLY",
            invariants_ok=True,
            correctness_delta=corr_delta,
            quality_delta=q_delta,
            reason="tests green + API preserved; no improvement signal to measure",
            evidence=evidence,
        )

    # Measured, but no benefit → discard. This is exactly the case the old
    # engine wrongly surfaced as a borderline "+0.0133" proposal.
    return EvalVerdict(
        "NO_CHANGE",
        invariants_ok=True,
        correctness_delta=corr_delta,
        quality_delta=q_delta,
        reason="measured, but no improvement beyond noise — discarded",
        evidence=evidence,
    )


# ── Held-out benchmark loader (immutable data) ───────────────────────────────


def _benchmarks_dir() -> Path:
    return Path(__file__).resolve().parent / "benchmarks"


def load_benchmark(
    target_file: str, *, benchmarks_dir: Optional[Path] = None
) -> list[dict]:
    """Return held-out benchmark tasks that apply to ``target_file``.

    Tasks live in ``app/self_improvement/benchmarks/*.json`` (TIER_IMMUTABLE so
    the Self-Improver cannot pad its own exam). Each file is
    ``{"target_prefixes": ["app/crews/research_crew.py", ...], "tasks": [...]}``;
    a task applies when any prefix matches ``target_file``. Returns [] when no
    benchmark targets this file — the common v1 case, which routes the change to
    an INVARIANTS_ONLY verdict (operator decides) rather than a fake delta.
    """
    base = benchmarks_dir or _benchmarks_dir()
    if not base.is_dir():
        return []
    tasks: list[dict] = []
    for fp in sorted(base.glob("*.json")):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("worktree_eval: bad benchmark file %s: %s", fp, exc)
            continue
        prefixes = data.get("target_prefixes") or []
        if any(target_file.startswith(p) for p in prefixes):
            for t in data.get("tasks", []):
                if isinstance(t, dict) and t.get("id") and t.get("input"):
                    tasks.append(t)
    return tasks


# ── Judge (immutable prompt; DGM-separated model) ────────────────────────────

# Injection point for tests ONLY. Production leaves this None and uses the
# immutable create_vetting_llm path. The judge prompt is fixed here so the
# Self-Improver cannot weaken the scoring rubric.
JudgeCallFn = Callable[[str], str]


def judge_outputs(
    tasks: list[dict],
    outputs: list[str],
    *,
    _llm_call: Optional[JudgeCallFn] = None,
) -> tuple[float, ...]:
    """Score each (task, output) pair in [0,1] using a DGM-separated judge.

    The judge model (vetting LLM — a different family from the generation
    models) and the rubric prompt are fixed here. ``_llm_call`` is a test-only
    seam; production passes None and uses ``create_vetting_llm``.
    """
    if not tasks or len(tasks) != len(outputs):
        return ()

    call = _llm_call
    if call is None:
        try:
            from app.llm_factory import create_vetting_llm

            judge = create_vetting_llm()
            call = lambda prompt: str(judge.call(prompt))  # noqa: E731
        except Exception as exc:  # pragma: no cover - runtime-only
            logger.warning("worktree_eval: judge LLM unavailable: %s", exc)
            return ()

    scores: list[float] = []
    for task, output in zip(tasks, outputs):
        rubric = task.get("rubric", "Answer is correct, specific, and directly addresses the task.")
        prompt = (
            "You are scoring an AI system's output on a held-out benchmark task.\n"
            f"Task: {str(task.get('input', ''))[:1500]}\n"
            f"Scoring rubric: {rubric}\n\n"
            f"Output to score:\n{str(output)[:3000]}\n\n"
            "Reply with ONLY a single decimal number from 0.0 to 1.0."
        )
        try:
            raw = call(prompt).strip()
            import re

            m = re.search(r"(\d+(?:\.\d+)?)", raw)
            scores.append(max(0.0, min(1.0, float(m.group(1)))) if m else 0.5)
        except Exception:
            scores.append(0.5)  # neutral on judge failure — never a free win
    return tuple(scores)
