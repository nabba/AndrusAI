"""Verified implementer — make a change that is *proven to run* before it is
ever proposed (2026-05-27).

Runs INSIDE the ephemeral evolver container, where the repo, the git worktree,
and ``pytest`` are co-located on one filesystem (``CODING_SESSION_BACKEND=local``).
That co-location is what makes ``coding_session.runner.run`` (in-process
subprocess) execute real tests against the changed code — with no host process
and no bridge. See ``feedback_docker_only_execution`` memory for why.

Contract
────────
A change is ``"green"`` only when the COMPLETE covering-test set AND a
synthesized API-preservation smoke test pass against the actual modified code in
the worktree. The framework-scaffold failure mode (the old engine's signature
bug) fails the smoke test on iteration 1 — it can't import or lacks ``.run``.

This replaces the old AVO ``_phase_implementation`` (8 KB-truncated blind read →
scaffold) + ``_phase_local_testing`` (AST-only) + ``_phase_self_critique``
(first-500-chars). Here the implementer sees the WHOLE file + the grounded
contract, and verification is by execution.

Tier
────
GENERATION (OPEN). It produces a candidate; it does NOT decide whether the
candidate is an *improvement* — that judgement is ``worktree_eval``
(TIER_IMMUTABLE). The split is the safety invariant: the Self-Improver can get
better at proposing, but can never lower the bar it must clear.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from app.coding_session import runtime
from app.coding_session.iterate import (
    IterateConfig,
    iterate_until_green,
    make_pyright_type_checker,
)
from app.coding_session.runner import run as runner_run
from app.self_improvement.change_spec import ChangeSpec

logger = logging.getLogger(__name__)

# editor_fn(spec, approach) -> new COMPLETE source for spec.target_file.
# Injected so this module has no LLM dependency and is unit-testable. The
# production editor (anchored search/replace, robust against the old
# whole-file-regeneration + output-truncation failure modes) is wired by the
# evolver job; tests pass a deterministic stub.
EditorFn = Callable[[ChangeSpec, str], str]


@dataclass
class ImplementResult:
    """Outcome of one verified-implement attempt. Returned on every path."""

    status: str  # green | tests_red | api_broken | edit_failed | no_session | error
    session_id: str = ""
    worktree_path: str = ""
    target_file: str = ""
    diff: str = ""
    changed_files: list[str] = field(default_factory=list)
    changed_file_contents: dict[str, str] = field(default_factory=dict)
    api_preserved: bool = False
    iterate: Optional[dict] = None
    notes: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        """A candidate worth evaluating: tests green AND public API intact."""
        return self.status == "green" and self.api_preserved

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "session_id": self.session_id,
            "worktree_path": self.worktree_path,
            "target_file": self.target_file,
            "diff_chars": len(self.diff),
            "changed_files": list(self.changed_files),
            "api_preserved": self.api_preserved,
            "iterate": self.iterate,
            "notes": list(self.notes),
        }


def _module_slug(module_path: str) -> str:
    return module_path.replace(".", "_") or "module"


def _write_smoke_test(worktree: Path, spec: ChangeSpec) -> str:
    """Write the API-preservation smoke test into the worktree's tests/ dir.

    Returns the repo-relative path. The file is left UNTRACKED so it never
    appears in ``git diff`` — it's a verification artifact, not part of the
    proposed change.
    """
    assertions = spec.preservation_assertions or [f"import {spec.module_path}"]
    body = "\n".join(f"    {line}" for line in assertions)
    content = (
        "# AUTO-GENERATED API-preservation smoke test (verified mutation engine).\n"
        "# Not part of the proposed change — left untracked, excluded from the diff.\n"
        "def test_api_preserved():\n"
        f"{body}\n"
    )
    rel = f"tests/_api_preservation__{_module_slug(spec.module_path)}.py"
    (worktree / "tests").mkdir(parents=True, exist_ok=True)
    (worktree / rel).write_text(content, encoding="utf-8")
    return rel


def _git_diff(worktree: Path, timeout_s: int) -> tuple[str, list[str]]:
    """Return (unified diff, changed tracked file paths). Untracked files (the
    smoke test) are intentionally excluded."""
    patch = runner_run(argv=["git", "diff"], cwd=str(worktree), timeout_s=timeout_s)
    names = runner_run(
        argv=["git", "diff", "--name-only"], cwd=str(worktree), timeout_s=timeout_s
    )
    changed = [ln.strip() for ln in (names.stdout or "").splitlines() if ln.strip()]
    return (patch.stdout or ""), changed


def implement_change(
    spec: ChangeSpec,
    approach: str,
    *,
    editor_fn: EditorFn,
    agent_id: str = "self_improver",
    manager: Any = None,
    base: str = "HEAD",
    config: Optional[IterateConfig] = None,
    diagnosis_fn: Optional[Callable[..., Any]] = None,
    run_timeout_s: int = 300,
) -> ImplementResult:
    """Open a worktree, apply the planned edit, and prove it runs.

    Parameters
    ----------
    spec
        The grounded contract (from ``change_spec.build_change_spec``).
    approach
        Plain-language description of what to change (from the planner).
    editor_fn
        ``(spec, approach) -> new_full_source``. Required — production wires an
        anchored-edit LLM editor; tests inject a stub.
    manager
        Coding-session manager. Defaults to ``runtime.get_manager()`` (Local
        backend inside the evolver container).
    config
        Iterate bounds. Defaults to a tight loop (6 iterations, $0.50, type
        check on) — a focused fix converges fast or surrenders.

    Returns
    -------
    ImplementResult
        Does NOT submit and does NOT evaluate-for-improvement — those are the
        caller's next steps (worktree_eval, then the operator-gated CR).
    """
    mgr = manager or runtime.get_manager()
    try:
        session = mgr.start(
            agent_id=agent_id,
            base=base,
            purpose=f"self-improve {spec.target_file}",
            worktree_root=runtime.worktree_root(),
        )
    except Exception as exc:
        return ImplementResult(
            status="no_session",
            target_file=spec.target_file,
            notes=[f"manager.start failed: {type(exc).__name__}: {exc}"],
        )

    worktree = Path(session.worktree_path)
    target_abs = worktree / spec.target_file
    base_result = ImplementResult(
        status="error",
        session_id=session.id,
        worktree_path=str(worktree),
        target_file=spec.target_file,
    )

    # 1. Read the COMPLETE current source from the worktree (authoritative for
    #    this base — never truncated).
    try:
        current = target_abs.read_text(encoding="utf-8")
    except OSError as exc:
        base_result.notes.append(f"cannot read target in worktree: {exc}")
        return base_result

    # 2. Produce the edit.
    try:
        new_source = editor_fn(spec, approach)
    except Exception as exc:
        base_result.status = "edit_failed"
        base_result.notes.append(f"editor_fn raised: {type(exc).__name__}: {exc}")
        return base_result

    if not new_source or new_source.strip() == current.strip():
        base_result.status = "edit_failed"
        base_result.notes.append("editor produced no change")
        return base_result

    # 3. Apply the edit in the worktree.
    try:
        target_abs.write_text(new_source, encoding="utf-8")
    except OSError as exc:
        base_result.notes.append(f"cannot write target in worktree: {exc}")
        return base_result

    # 4. Synthesize the API-preservation smoke test + assemble the test set:
    #    every covering test that exists in the worktree, plus the smoke test.
    smoke_rel = _write_smoke_test(worktree, spec)
    test_paths = [t for t in spec.covering_tests if (worktree / t).exists()]
    test_paths.append(smoke_rel)

    cfg = config or IterateConfig(max_iterations=6, budget_usd=0.50, run_type_check=True)

    def _test_runner():
        # `python3 -m pytest` (not bare `pytest`) prepends cwd to sys.path, so
        # `from app... import` resolves against the worktree checkout. python3
        # (not python) is the name reliably present on host + in the container.
        return runner_run(
            argv=["python3", "-m", "pytest", "-q",*test_paths],
            cwd=str(worktree),
            timeout_s=run_timeout_s,
        )

    def _reader(rel: str) -> str:
        return (worktree / rel).read_text(encoding="utf-8")

    def _writer(rel: str, content: str) -> None:
        (worktree / rel).write_text(content, encoding="utf-8")

    # 5. Run failing→diagnose→apply→retest until green or exhausted. The
    #    diagnosis here operates on the COMPLETE file with REAL test failures —
    #    the conditions under which even a full-file fixer preserves the API.
    outcome = iterate_until_green(
        target_file=spec.target_file,
        test_runner=_test_runner,
        file_reader=_reader,
        file_writer=_writer,
        config=cfg,
        diagnosis_fn=diagnosis_fn,
        type_checker=make_pyright_type_checker(worktree),
        pattern_signature=f"self_improve:{spec.target_file}",
        error_class="self_improve",
    )

    # 6. Independently confirm the public API survived (so the result carries
    #    api_preserved even when some covering test is red for another reason).
    smoke = runner_run(
        argv=["python3", "-m", "pytest", "-q",smoke_rel],
        cwd=str(worktree),
        timeout_s=run_timeout_s,
    )
    api_ok = bool(getattr(smoke, "ok", False))

    # 7. Capture the diff + the full new content of each changed (tracked) file
    #    — that's what the gateway needs to file a change-request per file.
    diff_text, changed = _git_diff(worktree, run_timeout_s)
    changed_contents: dict[str, str] = {}
    for name in changed:
        try:
            changed_contents[name] = (worktree / name).read_text(encoding="utf-8")
        except OSError:
            pass

    if outcome.status == "passed":
        status = "green"
    elif not api_ok:
        status = "api_broken"
    else:
        status = "tests_red"

    notes = [f"iterate={outcome.status}"]
    if outcome.last_decline_reason:
        notes.append(outcome.last_decline_reason[:200])
    if not api_ok:
        notes.append("API-preservation smoke test FAILED — public contract broken")

    return ImplementResult(
        status=status,
        session_id=session.id,
        worktree_path=str(worktree),
        target_file=spec.target_file,
        diff=diff_text,
        changed_files=changed,
        changed_file_contents=changed_contents,
        api_preserved=api_ok,
        iterate=outcome.as_jsonable(),
        notes=notes,
    )
