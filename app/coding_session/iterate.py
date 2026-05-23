"""Test-driven iterate loop — fix-until-green primitive (2026-05-20).

Phase 2 piece 2g. Sits between two already-shipped subsystems:

  * ``app.healing.structured_diagnosis`` — single-shot LLM fix proposal
    (error + traceback + file content → ``StructuredFix(path, new_content)``).
  * ``app.coding_session.runner`` — sandboxed allowlisted command
    execution inside a worktree (returns ``RunResult``).

The loop is:

  1. Run the test (caller-provided ``test_runner``).
  2. If green, return ``IterateOutcome(status="passed")``.
  3. If red, ask ``structured_diagnosis`` for a fix on ``target_file``.
  4. If diagnosis returns ``None`` or declined → ``no_fix_available``.
  5. If diagnosis returns a fix, apply it via the caller's ``file_writer``.
  6. Repeat.

Stop conditions (per safety bound, in this order):

  * Test passed → ``passed``
  * Max iterations reached → ``max_iterations``
  * Estimated cost would exceed budget → ``budget_exhausted``
  * No actionable fix available → ``no_fix_available``
  * Test runner crashes → ``test_runner_error``

Every dependency is injectable, so the loop is tested in isolation
without git, LLMs, or a sandbox. Production callers (the future
coder-agent integration) wire:

  * ``test_runner = lambda: coding_session.runner.run(argv=["pytest", path], ...)``
  * ``file_reader = lambda p: bridge.read_file(worktree_path + "/" + p)``
  * ``file_writer = lambda p, c: bridge.write_file(worktree_path + "/" + p, c)``
  * ``diagnosis_fn = generate_structured_fix`` (the default)

Safety semantics:

  * The loop does NOT call ``coding_session.submit`` — that's the
    operator-gated escape hatch. iterate_until_green only mutates
    files inside the worktree the caller is already holding.
  * Budget is the load-bearing cost bound. Default $2 ≈ 2000
    Haiku-tier diagnoses, which is far more than any sane loop
    needs. Operators tighten if they want.
  * Max iterations is the load-bearing infinite-loop bound. Default
    20 because even pathological bugs converge or surrender by then.
  * The diagnosis cost estimate is a fixed-rate model
    (``cost_per_diagnosis_usd``); precise per-call cost lives in the
    structured_diagnosis telemetry. Over-counting here is safe (the
    loop stops earlier than budget actually warrants).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ── Types ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class IterateConfig:
    """Loop bounds. All defaults are conservative — operators or
    callers tighten them for tighter sandboxes (a focused unit-test
    fix probably wants max_iterations=5, $0.10)."""

    max_iterations: int = 20
    budget_usd: float = 2.0
    cost_per_diagnosis_usd: float = 0.001
    # Phase 3 v2 (2026-05-22) — opt-in pyright type-check attached to
    # the final outcome. Observational only; never changes the
    # termination status. Combined with the master switch
    # ``pyright_sidecar_enabled``, both must be on for the check to
    # actually fire. When off, ``IterateOutcome.type_errors`` stays
    # empty regardless.
    run_type_check: bool = False


@dataclass
class IterateOutcome:
    """Final state of an :func:`iterate_until_green` call. Returned
    on every termination path (success or exhaustion)."""

    status: str  # see ITERATE_STATUSES
    iterations: int
    cost_usd: float
    fixes_applied: list[dict] = field(default_factory=list)
    last_test_result: Optional[dict] = None
    last_decline_reason: str = ""
    error_text: str = ""
    # Phase 3 v2 (2026-05-22) — populated by the post-loop pyright
    # check when ``IterateConfig.run_type_check`` is True AND a
    # ``type_checker`` callback was passed in. Stays empty otherwise.
    # Each entry is the dict form of a ``PyrightDiagnostic``.
    type_errors: list[dict] = field(default_factory=list)

    def as_jsonable(self) -> dict:
        return {
            "status": self.status,
            "iterations": self.iterations,
            "cost_usd": round(self.cost_usd, 6),
            "fixes_applied": list(self.fixes_applied),
            "last_test_result": self.last_test_result,
            "last_decline_reason": self.last_decline_reason,
            "error_text": self.error_text,
            "type_errors": list(self.type_errors),
        }


ITERATE_STATUSES = frozenset({
    "passed",
    "max_iterations",
    "budget_exhausted",
    "no_fix_available",
    "test_runner_error",
})


# Type aliases for injection.
TestRunnerFn = Callable[[], Any]      # returns RunResult-shape (.ok / .stderr / to_dict)
FileReaderFn = Callable[[str], str]   # workspace-relative path → content
FileWriterFn = Callable[[str, str], None]  # (path, content) → None
DiagnosisFn = Callable[..., Any]      # signature matches generate_structured_fix
# Phase 3 v2 (2026-05-22) — type-check callback. Takes the same
# workspace-relative path as the I/O callbacks, returns a list of
# diagnostic dicts (typically ``PyrightDiagnostic.to_dict()`` rows
# from the pyright sidecar). Decoupled by dict shape so iterate
# doesn't depend on the sidecar's dataclasses.
TypeCheckFn = Callable[[str], list[dict]]


# ── Loop ────────────────────────────────────────────────────────────


def iterate_until_green(
    *,
    target_file: str,
    test_runner: TestRunnerFn,
    file_reader: FileReaderFn,
    file_writer: FileWriterFn,
    config: Optional[IterateConfig] = None,
    diagnosis_fn: Optional[DiagnosisFn] = None,
    type_checker: Optional[TypeCheckFn] = None,
    pattern_signature: str = "iterate_loop",
    error_class: str = "iterate_loop",
) -> IterateOutcome:
    """Run failing test → diagnose → apply → retest, until green or
    exhausted.

    See module docstring for design + stop-condition semantics.

    Parameters
    ----------
    target_file
        The file the diagnosis is anchored on. Must exist (or
        ``file_reader`` raises). The diagnosis may suggest the same
        path or a different one — the loop applies whatever path the
        diagnosis returns.
    test_runner
        Callable returning the test outcome (RunResult-shape: ``ok``
        predicate + ``stderr``/``stdout`` strings + ``to_dict``
        method). Production wiring: ``coding_session.runner.run``.
    file_reader / file_writer
        I/O for the target file. Production wiring: the host bridge
        for the worktree.
    diagnosis_fn
        Defaults to ``app.healing.structured_diagnosis
        .generate_structured_fix``. Tests inject a stub.
    type_checker
        Optional callback that returns pyright-shape diagnostic dicts
        for ``target_file``. When supplied AND ``config.run_type_check``
        is True, the final outcome's ``type_errors`` field is
        populated with the error-severity entries. Observational —
        never changes the termination status. See
        :func:`make_pyright_type_checker` for the production wiring.

    Returns
    -------
    IterateOutcome
        Status + counters + per-iteration fix log + last test result.
    """
    outcome = _iterate_core(
        target_file=target_file,
        test_runner=test_runner,
        file_reader=file_reader,
        file_writer=file_writer,
        config=config,
        diagnosis_fn=diagnosis_fn,
        type_checker=type_checker,
        pattern_signature=pattern_signature,
        error_class=error_class,
    )

    cfg = config or IterateConfig()
    if cfg.run_type_check and type_checker is not None:
        try:
            diags = type_checker(target_file) or []
        except Exception:
            logger.debug(
                "iterate_until_green: type_checker raised",
                exc_info=True,
            )
            diags = []
        outcome.type_errors = [
            d for d in diags
            if isinstance(d, dict) and d.get("severity") == "error"
        ]
    return outcome


def _iterate_core(
    *,
    target_file: str,
    test_runner: TestRunnerFn,
    file_reader: FileReaderFn,
    file_writer: FileWriterFn,
    config: Optional[IterateConfig] = None,
    diagnosis_fn: Optional[DiagnosisFn] = None,
    type_checker: Optional[TypeCheckFn] = None,
    pattern_signature: str = "iterate_loop",
    error_class: str = "iterate_loop",
) -> IterateOutcome:
    """Inner loop body — extracted so the public wrapper can attach
    post-loop type-check info without duplicating the existing
    test/diagnose/apply state machine."""
    cfg = config or IterateConfig()
    fixes_applied: list[dict] = []
    cost = 0.0
    last_test_result: Optional[dict] = None
    last_decline = ""

    # Lazy import — keeps the production diagnosis_fn behind a fault-
    # tolerant default that's still trivially replaceable in tests.
    if diagnosis_fn is None:
        try:
            from app.healing.structured_diagnosis import (
                generate_structured_fix,
            )
            diagnosis_fn = generate_structured_fix
        except Exception:
            return IterateOutcome(
                status="no_fix_available",
                iterations=0,
                cost_usd=0.0,
                last_decline_reason=(
                    "structured_diagnosis unavailable; cannot iterate"
                ),
            )

    for i in range(cfg.max_iterations):
        # 1. Run the test
        try:
            result = test_runner()
        except Exception as exc:
            return IterateOutcome(
                status="test_runner_error",
                iterations=i,
                cost_usd=cost,
                fixes_applied=fixes_applied,
                last_test_result=last_test_result,
                error_text=f"{type(exc).__name__}: {exc}",
            )

        last_test_result = _result_to_dict(result)

        # 2. Green → done
        if _is_green(result):
            return IterateOutcome(
                status="passed",
                iterations=i,
                cost_usd=cost,
                fixes_applied=fixes_applied,
                last_test_result=last_test_result,
            )

        # 3. Budget check before paying for a diagnosis
        if (cost + cfg.cost_per_diagnosis_usd) > cfg.budget_usd:
            return IterateOutcome(
                status="budget_exhausted",
                iterations=i,
                cost_usd=cost,
                fixes_applied=fixes_applied,
                last_test_result=last_test_result,
                last_decline_reason=(
                    f"next diagnosis would exceed budget "
                    f"${cfg.budget_usd:.2f} (spent ${cost:.4f})"
                ),
            )

        # 4. Read current file content + ask for a fix
        try:
            file_content = file_reader(target_file)
        except Exception as exc:
            return IterateOutcome(
                status="no_fix_available",
                iterations=i,
                cost_usd=cost,
                fixes_applied=fixes_applied,
                last_test_result=last_test_result,
                error_text=f"file_reader: {type(exc).__name__}: {exc}",
            )

        # Phase 3 v2 follow-up (2026-05-22) — if type-checking is on,
        # query the type checker BEFORE diagnosis so the LLM sees
        # both the test failure AND open type errors in one prompt.
        # Empty list or None when off / no checker / checker raised.
        type_errors_hint: list[dict] | None = None
        if cfg.run_type_check and type_checker is not None:
            try:
                raw = type_checker(target_file) or []
                # Only error-severity rows belong in the prompt
                type_errors_hint = [
                    d for d in raw
                    if isinstance(d, dict) and d.get("severity") == "error"
                ]
                if not type_errors_hint:
                    type_errors_hint = None
            except Exception:
                logger.debug(
                    "_iterate_core: pre-diagnosis type_checker raised",
                    exc_info=True,
                )
                type_errors_hint = None

        try:
            # Pass the hint as a keyword — diagnosis_fn implementations
            # that don't know the kwarg should accept **kwargs OR we
            # fall back to a kwarg-less call.
            fix = _invoke_diagnosis_fn(
                diagnosis_fn,
                error_message=_stderr_of(result) or "(no stderr)",
                error_traceback=_stderr_of(result),
                file_path=target_file,
                file_content=file_content,
                pattern_signature=pattern_signature,
                error_class=error_class,
                type_errors_hint=type_errors_hint,
            )
        except Exception as exc:
            return IterateOutcome(
                status="no_fix_available",
                iterations=i,
                cost_usd=cost,
                fixes_applied=fixes_applied,
                last_test_result=last_test_result,
                last_decline_reason=(
                    f"diagnosis_fn raised: {type(exc).__name__}: {exc}"
                ),
            )

        cost += cfg.cost_per_diagnosis_usd

        # 5. No fix available → terminal "no_fix_available"
        if fix is None:
            return IterateOutcome(
                status="no_fix_available",
                iterations=i,
                cost_usd=cost,
                fixes_applied=fixes_applied,
                last_test_result=last_test_result,
                last_decline_reason="diagnosis returned None",
            )
        if getattr(fix, "declined", False) or not getattr(fix, "is_actionable", False):
            last_decline = getattr(fix, "decline_reason", "") or "declined"
            return IterateOutcome(
                status="no_fix_available",
                iterations=i,
                cost_usd=cost,
                fixes_applied=fixes_applied,
                last_test_result=last_test_result,
                last_decline_reason=last_decline,
            )

        # 6. Apply the fix
        fix_path = getattr(fix, "path", "") or target_file
        new_content = getattr(fix, "new_content", "")
        confidence = float(getattr(fix, "confidence", 0.0) or 0.0)
        try:
            file_writer(fix_path, new_content)
        except Exception as exc:
            return IterateOutcome(
                status="no_fix_available",
                iterations=i,
                cost_usd=cost,
                fixes_applied=fixes_applied,
                last_test_result=last_test_result,
                error_text=(
                    f"file_writer: {type(exc).__name__}: {exc}"
                ),
            )

        fixes_applied.append({
            "iteration": i,
            "path": fix_path,
            "confidence": confidence,
            "reasoning": (getattr(fix, "reasoning", "") or "")[:200],
        })

    # Out of iterations without green
    return IterateOutcome(
        status="max_iterations",
        iterations=cfg.max_iterations,
        cost_usd=cost,
        fixes_applied=fixes_applied,
        last_test_result=last_test_result,
        last_decline_reason=(
            f"reached max_iterations={cfg.max_iterations} without green test"
        ),
    )


# ── Helpers ─────────────────────────────────────────────────────────


def _invoke_diagnosis_fn(fn, **kwargs):
    """Call ``fn`` with the subset of ``kwargs`` that its signature
    actually accepts.

    Phase B.1 cleanup (2026-05-22): introspects the callable via
    :func:`inspect.signature` rather than the prior pattern of calling
    with all kwargs and catching ``TypeError`` matching a specific
    string ("type_errors_hint"). The introspection approach is
    correct mechanism for "drop kwargs the callee doesn't accept" —
    no exception-control-flow, no string matching, deterministic.

    Behavior:
      * If ``fn`` accepts ``**kwargs``, all kwargs pass through.
      * Otherwise, only kwargs naming a positional-or-keyword or
        keyword-only parameter pass through; the rest are dropped.
      * If ``inspect.signature(fn)`` itself fails (some C-implemented
        builtins lack a signature), we fall back to passing all
        kwargs and let Python raise normally.
    """
    import inspect
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return fn(**kwargs)

    # If the function takes **kwargs, all our kwargs are accepted.
    if any(
        p.kind is inspect.Parameter.VAR_KEYWORD
        for p in sig.parameters.values()
    ):
        return fn(**kwargs)

    accepted = {
        name
        for name, p in sig.parameters.items()
        if p.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    }
    filtered = {k: v for k, v in kwargs.items() if k in accepted}
    return fn(**filtered)


def _is_green(result: Any) -> bool:
    """Treat a result as green when its ``ok`` predicate is True.

    Defensive against non-RunResult shapes — if ``ok`` isn't a usable
    attribute, fall back to checking ``exit_code == 0``."""
    ok_attr = getattr(result, "ok", None)
    if isinstance(ok_attr, bool):
        return ok_attr
    # Fallback for stubbed result objects in tests
    try:
        return int(getattr(result, "exit_code", 1)) == 0
    except (TypeError, ValueError):
        return False


def _stderr_of(result: Any) -> str:
    """Extract stderr from a RunResult-shape, defensive."""
    s = getattr(result, "stderr", "") or ""
    return str(s)


def _result_to_dict(result: Any) -> Optional[dict]:
    """Serialise the result for the outcome's ``last_test_result``.
    Falls back to a minimal projection if ``to_dict`` is unavailable."""
    if result is None:
        return None
    to_dict = getattr(result, "to_dict", None)
    if callable(to_dict):
        try:
            return to_dict()
        except Exception:
            pass
    # Minimal projection — best effort for stubs without to_dict
    return {
        "exit_code": getattr(result, "exit_code", None),
        "stderr": (_stderr_of(result) or "")[:500],
        "stdout": (str(getattr(result, "stdout", "")) or "")[:500],
        "ok": _is_green(result),
    }


# ── Production wiring helpers ───────────────────────────────────────


def make_pyright_type_checker(worktree_root: "Any") -> TypeCheckFn:
    """Build a ``type_checker`` callable wired to the pyright sidecar.

    The returned closure resolves the workspace-relative ``target_file``
    against ``worktree_root`` and invokes the sidecar's ``check_file``.
    Diagnostics are returned as plain dicts so ``iterate_until_green``
    stays decoupled from ``pyright_sidecar``'s dataclasses.

    Failure-isolated: a sick sidecar / missing pyright binary / parse
    error all surface as an empty list rather than raising.

    Typical production wiring::

        from pathlib import Path
        outcome = iterate_until_green(
            ...,
            config=IterateConfig(run_type_check=True),
            type_checker=make_pyright_type_checker(Path(worktree_path)),
        )
    """
    from pathlib import Path

    root = Path(worktree_root)

    def _checker(target_file: str) -> list[dict]:
        try:
            from app.code_intel.pyright_sidecar import check_file
        except Exception:
            return []
        try:
            report = check_file(root / target_file)
        except Exception:
            logger.debug(
                "make_pyright_type_checker: check_file raised", exc_info=True,
            )
            return []
        return [d.to_dict() for d in report.diagnostics]

    return _checker
