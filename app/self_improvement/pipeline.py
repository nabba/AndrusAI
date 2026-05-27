"""pipeline — the verified mutation engine, composed end-to-end (2026-05-27).

Ties the three pieces together inside the evolver container:

    build_change_spec   (ground: truncation-free contract)
        ↓
    implement_change    (edit in a worktree, prove it RUNS — tests + API smoke)
        ↓
    correctness + quality eval against the CHANGED code (baseline vs candidate)
        ↓
    compute_verdict     (the immutable judgement)

The result is a ``PipelineResult`` whose ``verdict`` is computed from signals
produced by *actually running the changed code* — never the old "delta on a
shadow path the code never loaded."

Tier: GENERATION/OPEN. It calls the immutable judge (``worktree_eval``) but does
not embody the judgement itself. It deliberately does NOT read the master switch
(``runtime_settings``) — gating is the orchestrator's job — so this module stays
free of the pydantic/runtime dependency and is testable in isolation with real
git + real pytest (only the LLM editor/judge are injected).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from app.coding_session import runtime
from app.coding_session.iterate import IterateConfig
from app.coding_session.runner import run as runner_run
from app.self_improvement.change_spec import build_change_spec
from app.self_improvement.verified_implementer import implement_change
from app.self_improvement.worktree_eval import (
    CorrectnessResult,
    EvalThresholds,
    QualityResult,
    compute_verdict,
    judge_outputs,
    load_benchmark,
)

logger = logging.getLogger(__name__)

# (code_root, task) -> output text from running the task through the real entry
# point in that code root. Injected; production wires a subprocess invocation,
# tests wire a stub.
EntryPointRunner = Callable[[str, dict], str]
JudgeCall = Callable[[str], str]

_FAIL_LINE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)")


@dataclass
class PipelineResult:
    """End-to-end outcome. ``proposable`` mirrors the verdict — the orchestrator
    files a change-request only when this is True."""

    target_file: str
    approach: str
    verdict: dict  # EvalVerdict.to_dict()
    proposable: bool
    status: str  # implement status: green | api_broken | tests_red | edit_failed | no_session | error
    diff: str = ""
    changed_file_contents: dict[str, str] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_file": self.target_file,
            "approach": self.approach,
            "verdict": self.verdict,
            "proposable": self.proposable,
            "status": self.status,
            "diff": self.diff,
            "changed_files": sorted(self.changed_file_contents),
            # Full new content per changed file — REQUIRED for the gateway to
            # file change-requests, since the evolver worktree is destroyed when
            # the ephemeral container exits (results travel back via this JSON).
            "changed_file_contents": dict(self.changed_file_contents),
            "evidence": self.evidence,
            "notes": list(self.notes),
        }


def _failing_test_ids(
    test_files: list[str], cwd: str, timeout_s: int
) -> frozenset[str]:
    """Run ``test_files`` and return the set of failing/erroring node ids.

    Uses ``-rfE`` so pytest prints a ``FAILED <nodeid>`` / ``ERROR <nodeid>``
    summary line per failure, and ``--tb=no`` to keep output small. ``python3
    -m pytest`` (not bare pytest) puts cwd on sys.path so ``app`` imports.
    """
    if not test_files:
        return frozenset()
    result = runner_run(
        argv=["python3", "-m", "pytest", "-rfE", "-q", "--tb=no", *test_files],
        cwd=cwd,
        timeout_s=timeout_s,
    )
    ids: set[str] = set()
    for line in (result.stdout or "").splitlines():
        m = _FAIL_LINE.match(line.strip())
        if m:
            ids.add(m.group(1))
    return frozenset(ids)


def run_pipeline(
    target_file: str,
    approach: str,
    *,
    editor_fn: Callable[..., str],
    manager: Any = None,
    base: str = "HEAD",
    root: Optional[Path | str] = None,
    config: Optional[IterateConfig] = None,
    diagnosis_fn: Optional[Callable[..., Any]] = None,
    entry_point_runner: Optional[EntryPointRunner] = None,
    judge_call: Optional[JudgeCall] = None,
    quality_tasks: Optional[list[dict]] = None,
    thresholds: EvalThresholds = EvalThresholds(),
    run_timeout_s: int = 300,
) -> PipelineResult:
    """Run the full ground→implement→evaluate→verdict pipeline.

    Parameters of note
    ------------------
    editor_fn
        ``(spec, approach) -> new_full_source``. Required.
    entry_point_runner / judge_call
        Optional quality-eval wiring. When both are present AND a benchmark
        targets this file, the quality axis is measured. Absent → correctness
        only (the common v1 case).
    quality_tasks
        Override the held-out benchmark lookup (tests pass tasks directly).
    """
    mgr = manager or runtime.get_manager()

    # 1. Ground.
    spec = build_change_spec(target_file, root=root, refresh=True)

    # 2. Implement + prove it runs.
    impl = implement_change(
        spec,
        approach,
        editor_fn=editor_fn,
        manager=mgr,
        base=base,
        config=config,
        diagnosis_fn=diagnosis_fn,
        run_timeout_s=run_timeout_s,
    )

    # 3. Invariants gate: a candidate that doesn't run is never an improvement.
    if not impl.succeeded:
        verdict = compute_verdict(invariants_ok=False)
        verdict.notes.append(f"implement status={impl.status}: {'; '.join(impl.notes)}")
        return PipelineResult(
            target_file=spec.target_file,
            approach=approach,
            verdict=verdict.to_dict(),
            proposable=verdict.proposable,
            status=impl.status,
            diff=impl.diff,
            changed_file_contents=impl.changed_file_contents,
            notes=list(impl.notes),
        )

    candidate_root = impl.worktree_path

    # 4. Baseline worktree (unedited @ base) for the comparative signals.
    baseline_root = ""
    try:
        baseline = mgr.start(
            agent_id="self_improver_baseline",
            base=base,
            purpose=f"baseline for {spec.target_file}",
            worktree_root=runtime.worktree_root(),
        )
        baseline_root = baseline.worktree_path
    except Exception as exc:
        logger.warning("pipeline: baseline worktree failed: %s", exc)

    # 5. Correctness signal (cheap, deterministic): which covering tests fail at
    #    baseline? The candidate is green by construction (impl.succeeded), so
    #    candidate_failed over the covering set is empty → any baseline failure
    #    the change resolves is a real fix.
    correctness = None
    if baseline_root and spec.covering_tests:
        baseline_failed = _failing_test_ids(
            spec.covering_tests, baseline_root, run_timeout_s
        )
        correctness = CorrectnessResult(
            baseline_failed=baseline_failed,
            candidate_failed=frozenset(),
            ran=True,
        )

    # 6. Quality signal (optional, expensive): run the held-out benchmark through
    #    the real entry point on baseline vs candidate, judge paired.
    quality = None
    tasks = quality_tasks if quality_tasks is not None else load_benchmark(spec.target_file)
    if tasks and entry_point_runner is not None and baseline_root:
        try:
            base_out = [entry_point_runner(baseline_root, t) for t in tasks]
            cand_out = [entry_point_runner(candidate_root, t) for t in tasks]
            base_scores = judge_outputs(tasks, base_out, _llm_call=judge_call)
            cand_scores = judge_outputs(tasks, cand_out, _llm_call=judge_call)
            quality = QualityResult(
                baseline_scores=base_scores, candidate_scores=cand_scores
            )
        except Exception as exc:
            logger.warning("pipeline: quality eval failed: %s", exc)

    # 7. Verdict (immutable judgement).
    verdict = compute_verdict(
        invariants_ok=True,
        correctness=correctness,
        quality=quality,
        thresholds=thresholds,
    )

    return PipelineResult(
        target_file=spec.target_file,
        approach=approach,
        verdict=verdict.to_dict(),
        proposable=verdict.proposable,
        status=impl.status,
        diff=impl.diff,
        changed_file_contents=impl.changed_file_contents,
        evidence=verdict.evidence,
        notes=list(verdict.notes) + [f"iterate={impl.iterate.get('status') if impl.iterate else '?'}"],
    )
