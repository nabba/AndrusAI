"""app.research.experiment_repair — bounded design→run→repair for the experiment spine.

The Phase-C ``run_experiment`` step runs an LLM-authored measurement script in
the sandbox exactly ONCE: a script that errors, times out, or prints no
measurement is recorded as a failure and the spine moves on (observed live —
only ~1 in 4 runs produced a clean measurement). This module closes that gap by
wrapping the one-shot run in the *existing* test-driven repair loop
(:func:`app.coding_session.iterate.iterate_until_green`): run the script; if it
didn't produce a usable measurement, ask a focused code-gen completion to repair
it, and re-run — bounded by ``max_rounds`` and a per-run dollar budget.

It owns no new machinery. ``iterate_until_green`` is consumed verbatim; the only
research-specific seams are:

  1. a RunResult-shape adapter (:class:`_RunResult`) whose ``.ok`` means
     "ran clean AND emitted a measurement" (not merely "exited 0"); and
  2. a script-rewrite ``diagnosis_fn`` that turns the failure + the failing
     script into a new script via the LLM factory's code-gen role.

The script is held in memory — NO worktree — so ``file_reader``/``file_writer``
back onto a single slot and the loop drives it just as it drives a worktree
file. The repair completion happens HERE (gateway-side), so the experiment
container keeps ``network=none``: it only ever runs a finished script.

Every external call (``experiment_fn``, ``repair_fn``) is injected and failure
isolated, and ``iterate_until_green`` never raises, so this returns a valid
experiment envelope (the same shape ``experiment_fn`` returns) on every path —
``app.research.run``'s ``analyze_result`` consumes it unchanged.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Defaults: a measurement script is small, so a handful of rounds + a few cents
# is plenty. max_rounds is the load-bearing infinite-loop bound; the budget is a
# fixed-rate estimate inside iterate_until_green (over-counting only stops earlier).
_DEFAULT_MAX_ROUNDS = 3
_DEFAULT_BUDGET_USD = 0.50
_SCRIPT_PATH = "experiment.py"  # the in-memory "file" the loop anchors on


def _measurement_present(envelope: Any) -> bool:
    """A run counts as green only when it ran clean AND emitted a measurement.

    Mirrors the design-prompt contract ("prints its measurements to stdout as
    the result"): a clean exit with empty stdout is NOT a measurement — it's a
    script that forgot to print, and is worth a repair round.
    """
    if not isinstance(envelope, dict) or not envelope.get("ok"):
        return False  # transport/spawn failure
    result = envelope.get("result")
    if not isinstance(result, dict):
        return False
    return bool(result.get("ok")) and bool(str(result.get("stdout") or "").strip())


def _error_text(envelope: Any) -> str:
    """The failure description fed to the repair completion."""
    if not isinstance(envelope, dict) or not envelope.get("ok"):
        err = envelope.get("error") if isinstance(envelope, dict) else None
        return str(err or "experiment failed to run")
    result = envelope.get("result")
    if isinstance(result, dict):
        if result.get("timed_out"):
            return "the script timed out before completing"
        stderr = str(result.get("stderr") or "").strip()
        if stderr:
            return stderr
        if not str(result.get("stdout") or "").strip():
            return "the script ran cleanly but printed no measurement to stdout"
    return "the script did not produce a usable measurement"


@dataclass
class _RunResult:
    """RunResult-shape adapter for ``iterate_until_green`` (``.ok``/``.stderr``/``to_dict``)."""

    ok: bool
    stderr: str
    stdout: str

    def to_dict(self) -> dict:
        return {"ok": self.ok, "stderr": self.stderr[:500], "stdout": self.stdout[:500]}


@dataclass
class _ScriptFix:
    """StructuredFix-shape the loop expects back from ``diagnosis_fn``."""

    declined: bool
    is_actionable: bool
    path: str
    new_content: str
    confidence: float = 0.6
    reasoning: str = ""
    decline_reason: str = ""


def _build_repair_prompt(*, goal: str, script: str, error: str) -> str:
    parts = [
        "The Python 3 experiment script below failed to produce a usable "
        "measurement. Rewrite it so it runs to completion and prints its "
        "measurement(s) to stdout. Keep it self-contained: standard library "
        "only, no network access, no input files, finishing well under a "
        "minute. Output exactly ONE Python code block and nothing else.",
    ]
    if goal:
        parts.append(f"\nResearch question: {goal}")
    parts.append("\nThe failure was:\n" + (error or "(no diagnostics)")[:1500])
    parts.append("\nThe failing script:\n```python\n" + script + "\n```")
    return "\n".join(parts)


def _default_code_completion(prompt: str) -> str:
    """Focused code-gen completion via the LLM factory (the sole LLM path)."""
    try:
        from app.llm_factory import chat_completion_for_role

        handle = chat_completion_for_role(role="coding", task_hint="research experiment repair")
        resp = handle.create(messages=[{"role": "user", "content": prompt}], max_tokens=2500)
        return resp.choices[0].message.content or ""
    except Exception:
        logger.debug("experiment_repair: code completion failed", exc_info=True)
        return ""


def _make_diagnosis_fn(
    repair_fn: Callable[[str], str],
    extract_fn: Callable[[str], str],
    goal: str,
) -> Callable[..., Optional[_ScriptFix]]:
    """Build the loop's ``diagnosis_fn``: failing script + error → new script."""

    def diagnosis_fn(*, error_message: str, error_traceback: str, file_path: str, file_content: str, **_kw):
        prompt = _build_repair_prompt(
            goal=goal, script=file_content, error=error_traceback or error_message
        )
        try:
            reply = repair_fn(prompt) or ""
        except Exception:
            logger.debug("experiment_repair: repair_fn raised", exc_info=True)
            return _ScriptFix(
                declined=True,
                is_actionable=False,
                path=file_path,
                new_content="",
                decline_reason="repair completion failed",
            )
        new_script = extract_fn(reply)
        if not new_script or new_script.strip() == file_content.strip():
            # No usable / no *different* script — stop the loop cleanly.
            return _ScriptFix(
                declined=True,
                is_actionable=False,
                path=file_path,
                new_content="",
                decline_reason="no new script produced",
            )
        return _ScriptFix(
            declined=False,
            is_actionable=True,
            path=file_path,
            new_content=new_script,
            reasoning="rewrote failing experiment script",
        )

    return diagnosis_fn


def run_experiment_with_repair(
    initial_script: str,
    *,
    experiment_fn: Callable[..., dict],
    extract_fn: Callable[[str], str],
    timeout_s: int = 300,
    max_rounds: int = _DEFAULT_MAX_ROUNDS,
    budget_usd: float = _DEFAULT_BUDGET_USD,
    repair_fn: Optional[Callable[[str], str]] = None,
    goal: str = "",
) -> dict:
    """Run ``initial_script``; on failure, repair-and-rerun up to ``max_rounds``.

    ``experiment_fn`` is ``run_experiment_script``-shaped — ``(script, *,
    timeout_s) -> envelope`` where ``envelope`` is
    ``{"ok": bool, "result": {...}}`` or ``{"ok": False, "error": "..."}``.
    ``extract_fn`` pulls a runnable script out of the repair reply (the same
    ``_extract_python_script`` the design step uses).

    Returns the final experiment envelope — the same shape ``experiment_fn``
    returns, with an extra ``"repair"`` key ({status, rounds, fixes}) for
    observability (``analyze_result`` ignores unknown keys). Never raises.

    ``max_rounds`` repair attempts are each re-tested, so the script runs up to
    ``max_rounds + 1`` times (the initial run plus one re-test per repair).
    """
    from app.coding_session.iterate import IterateConfig, iterate_until_green

    if repair_fn is None:
        repair_fn = _default_code_completion

    holder: dict[str, Any] = {"script": initial_script, "last_envelope": None}

    def file_reader(_path: str) -> str:
        return holder["script"]

    def file_writer(_path: str, content: str) -> None:
        holder["script"] = content

    def test_runner() -> _RunResult:
        env = experiment_fn(holder["script"], timeout_s=timeout_s)
        if not isinstance(env, dict):
            env = {"ok": False, "error": "experiment_fn returned a non-dict result"}
        holder["last_envelope"] = env
        result = env.get("result") if isinstance(env.get("result"), dict) else {}
        return _RunResult(
            ok=_measurement_present(env),
            stderr=_error_text(env),
            stdout=str(result.get("stdout") or ""),
        )

    outcome = iterate_until_green(
        target_file=_SCRIPT_PATH,
        test_runner=test_runner,
        file_reader=file_reader,
        file_writer=file_writer,
        config=IterateConfig(max_iterations=max(1, max_rounds) + 1, budget_usd=budget_usd),
        diagnosis_fn=_make_diagnosis_fn(repair_fn, extract_fn, goal),
        pattern_signature="research_experiment",
        error_class="research_experiment",
    )

    env = holder["last_envelope"]
    if not isinstance(env, dict):
        env = {"ok": False, "error": "experiment never ran"}
    env = dict(env)
    env["repair"] = {
        "status": outcome.status,
        "rounds": outcome.iterations,
        "fixes": len(outcome.fixes_applied),
    }
    return env


__all__ = ["run_experiment_with_repair"]
