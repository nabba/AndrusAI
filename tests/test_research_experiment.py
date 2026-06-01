"""Host-safe tests for the Phase C experiment spine.

Covers ``app.research.experiment_job`` (the in-container harness),
``app.research.experiment`` (the gateway-side shipper), and the three
experiment branches added to ``app.research.run``'s adapter + planner.

The experiment runs FULLY AUTONOMOUSLY — there is no per-experiment operator
gate — so these tests pin the three bounds that replace the gate:

  1. the ``research_experiments_enabled`` master switch (``enabled_fn``);
  2. the ephemeral-container transport (injected fake — no Docker ever runs);
  3. the per-run executor state machine (real — drives plan → BLOCKED/COMPLETED).

Every external seam is injected, so no Docker / LLM / crewai / ChromaDB loads.
The epistemic ledger IS exercised for real (it is host-safe and fully
failure-isolated), proving the analyze_result claim-emit path end to end.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

import app.research.run as R
from app.research import experiment as EXP
from app.research import experiment_job as JOB
from app.autonomous_executor.driver import CommanderResult
from app.autonomous_executor.models import (
    ExecutorRun,
    ExecutorStatus,
    ExecutorStep,
    StepStatus,
)


# ── experiment_job: the in-container harness ──────────────────────────────────


def test_run_experiment_clean_run():
    out = JOB.run_experiment({"script": "print('hello'); print(2 + 2)"})
    assert out["ok"] is True
    assert out["returncode"] == 0
    assert "hello" in out["stdout"]
    assert "4" in out["stdout"]
    assert out["timed_out"] is False


def test_run_experiment_nonzero_exit():
    out = JOB.run_experiment({"script": "import sys; sys.exit(3)"})
    assert out["ok"] is False
    assert out["returncode"] == 3
    assert out["timed_out"] is False


def test_run_experiment_traceback_is_nonzero():
    out = JOB.run_experiment({"script": "raise RuntimeError('boom')"})
    assert out["ok"] is False
    assert out["returncode"] != 0
    assert "boom" in out["stderr"]


def test_run_experiment_missing_script():
    for bad in ({}, {"script": ""}, {"script": "   "}, {"script": 123}):
        out = JOB.run_experiment(bad)
        assert out["ok"] is False
        assert "script" in out["stderr"]


def test_run_experiment_fast_script_under_huge_timeout_request():
    # The harness clamps timeout_s to its ceiling; a fast script still runs.
    out = JOB.run_experiment({"script": "print('x')", "timeout_s": 999999})
    assert out["ok"] is True


def test_extract_result_roundtrip():
    payload = {
        "ok": True,
        "result": {"ok": True, "returncode": 0, "stdout": "m=1", "stderr": "", "timed_out": False},
    }
    logs = f"noise\n{JOB._RESULT_BEGIN}{json.dumps(payload)}{JOB._RESULT_END}\nmore noise\n"
    assert JOB.extract_result(logs) == payload


def test_extract_result_missing_sentinel_raises():
    with pytest.raises(ValueError):
        JOB.extract_result("just some logs, no sentinel here")


def test_extract_result_takes_last_sentinel():
    a = {"ok": True, "result": {"stdout": "first"}}
    b = {"ok": True, "result": {"stdout": "second"}}
    logs = (
        JOB._RESULT_BEGIN + json.dumps(a) + JOB._RESULT_END + "\n"
        + JOB._RESULT_BEGIN + json.dumps(b) + JOB._RESULT_END + "\n"
    )
    assert JOB.extract_result(logs)["result"]["stdout"] == "second"


# ── experiment.run_experiment_script: gateway-side shipper (fake transport) ────


def _fake_transport(stdout: str = "answer=42", *, ok: bool = True, rc: int = 0):
    """A docker-proxy transport double that records the create payload and
    returns a sentinel-wrapped result from the logs endpoint."""
    seen: dict = {"calls": []}

    def tx(method, path, body=None, timeout=None):
        seen["calls"].append((method, path))
        if method == "POST" and path == "/containers/create":
            env = {e.split("=", 1)[0]: e.split("=", 1)[1] for e in body["Env"]}
            seen["job"] = json.loads(env["AAI_EXPERIMENT_JOB"])
            seen["entrypoint"] = body.get("Entrypoint")
            seen["memory"] = body["HostConfig"]["Memory"]
            return 201, json.dumps({"Id": "c123"}).encode()
        if path.endswith("/start"):
            return 204, b""
        if path.endswith("/wait"):
            return 200, b"{}"
        if "/logs" in path:
            payload = {
                "ok": True,
                "result": {"ok": ok, "returncode": rc, "stdout": stdout, "stderr": "", "timed_out": False},
            }
            logs = JOB._RESULT_BEGIN + json.dumps(payload) + JOB._RESULT_END + "\n"
            return 200, logs.encode()
        if method == "DELETE":
            return 200, b""
        return 200, b"{}"

    return tx, seen


def test_run_experiment_script_via_fake_transport():
    tx, seen = _fake_transport(stdout="answer=42")
    out = EXP.run_experiment_script("print('answer=42')", timeout_s=30, transport=tx)
    assert out["ok"] is True
    assert out["result"]["stdout"] == "answer=42"
    # the script shipped via AAI_EXPERIMENT_JOB with the experiment entrypoint
    assert seen["job"]["script"] == "print('answer=42')"
    assert seen["entrypoint"] == EXP._EXPERIMENT_ENTRYPOINT
    # the experiment gets the smaller (2 GB) memory cap, not the evolver's 4 GB
    assert seen["memory"] == EXP._EXPERIMENT_MEMORY_BYTES


def test_run_experiment_script_create_failure_returns_error():
    def fail_create(method, path, body=None, timeout=None):
        if path == "/containers/create":
            return 500, b"nope"
        return 200, b"{}"

    out = EXP.run_experiment_script("print('x')", transport=fail_create)
    assert out["ok"] is False
    assert "create failed" in out["error"]


# ── Planner: the seven-step experiment chain ──────────────────────────────────


def _hints(steps):
    return [s.crew_hint for s in steps]


def test_plan_research_experiment_emits_seven_steps_in_order():
    steps = R.plan_research("optimise retrieval", experiment=True)
    assert _hints(steps) == [
        R.HINT_LITERATURE,
        R.HINT_HYPOTHESES,
        R.HINT_DESIGN_EXPERIMENT,
        R.HINT_RUN_EXPERIMENT,
        R.HINT_ANALYZE_RESULT,
        R.HINT_DRAFT,
        R.HINT_GATE,
    ]


def test_plan_research_default_is_unchanged_five_steps():
    # Phase C must not change the default Phase-3 contract.
    steps = R.plan_research("x")
    assert len(steps) == 5
    assert R.HINT_INVESTIGATE in _hints(steps)
    assert R.HINT_DESIGN_EXPERIMENT not in _hints(steps)


def test_plan_research_experiment_with_synthesize():
    steps = R.plan_research("x", experiment=True, synthesize=True)
    assert _hints(steps)[-1] == R.HINT_SYNTHESIZE
    assert R.HINT_RUN_EXPERIMENT in _hints(steps)
    assert R.HINT_INVESTIGATE not in _hints(steps)


def test_build_research_run_experiment_seven_prepopulated_steps():
    run = R.build_research_run("topic", experiment=True)
    assert run.status is ExecutorStatus.PLANNING
    assert _hints(run.plan) == [
        R.HINT_LITERATURE,
        R.HINT_HYPOTHESES,
        R.HINT_DESIGN_EXPERIMENT,
        R.HINT_RUN_EXPERIMENT,
        R.HINT_ANALYZE_RESULT,
        R.HINT_DRAFT,
        R.HINT_GATE,
    ]


# ── Script extraction ─────────────────────────────────────────────────────────


def test_extract_python_script_from_python_fence():
    text = "Here is the experiment:\n```python\nprint('measured', 7)\n```\nDone."
    assert R._extract_python_script(text) == "print('measured', 7)"


def test_extract_python_script_from_bare_fence():
    text = "```\nimport math\nprint(math.pi)\n```"
    assert R._extract_python_script(text) == "import math\nprint(math.pi)"


def test_extract_python_script_bare_heuristic():
    text = "import sys\nprint('hi')"
    assert R._extract_python_script(text) == "import sys\nprint('hi')"


def test_extract_python_script_empty_for_prose():
    assert R._extract_python_script("I cannot design an experiment for this.") == ""
    assert R._extract_python_script("") == ""


# ── Adapter dispatch (experiment branches) ────────────────────────────────────


@dataclass
class _GateStub:
    action: str
    final_text: str = ""
    user_visible_reason: str = ""


_DEFAULT_SCRIPT_REPLY = "```python\nprint('x=1')\n```"
_CLEAN_ENVELOPE = {
    "ok": True,
    "result": {"ok": True, "returncode": 0, "stdout": "x=1", "stderr": "", "timed_out": False},
}


def _exp_seams(*, enabled=True, exp_result=None, gate=None, design_reply=_DEFAULT_SCRIPT_REPLY, draft_text="DRAFT"):
    """All seven adapter seams injected — fully host-safe, no lazy imports fire."""
    cap: dict = {"experiment_calls": [], "gate_calls": []}

    def search_fn(goal):
        return []

    def propose_fn(question, *, literature=None, **kw):
        return [{"text": "leading hypothesis", "rank": 1}]

    def commander_fn(step, run):
        # Fallback only — every research:* hint routes through a dedicated seam.
        return CommanderResult(text="COMMANDER-FALLBACK")

    def design_fn(prompt):
        return design_reply

    def draft_fn(prompt):
        return draft_text

    def gate_fn(*, proposal_text, task_id, verdict):
        return (None, "")  # draft gate clears

    def experiment_fn(script, *, timeout_s=300):
        cap["experiment_calls"].append({"script": script, "timeout_s": timeout_s})
        return exp_result if exp_result is not None else _CLEAN_ENVELOPE

    def enabled_fn():
        return enabled

    def gate_output_fn(*, proposal_text, task_id, triggering_claim_id=None):
        cap["gate_calls"].append({"proposal_text": proposal_text, "task_id": task_id})
        return gate

    seams = dict(
        search_fn=search_fn,
        propose_fn=propose_fn,
        commander_fn=commander_fn,
        gate_fn=gate_fn,
        experiment_fn=experiment_fn,
        enabled_fn=enabled_fn,
        gate_output_fn=gate_output_fn,
        design_fn=design_fn,
        draft_fn=draft_fn,
    )
    return seams, cap


def _exp_run(goal="optimise retrieval"):
    run = R.build_research_run(goal, experiment=True)
    run.transition(ExecutorStatus.RUNNING)
    return run


def _step(run, hint):
    return next(s for s in run.plan if s.crew_hint == hint)


def _complete(run, hint, text):
    step = _step(run, hint)
    step.status = StepStatus.COMPLETED
    step.result_text = text
    return step


def test_design_experiment_uses_design_seam():
    seams, _ = _exp_seams()
    adapter = R.make_research_adapter(**seams)
    run = _exp_run()
    out = adapter(_step(run, R.HINT_DESIGN_EXPERIMENT), run)
    assert "print('x=1')" in out.text  # design_fn's script reply passed through


def test_run_experiment_runs_when_enabled_and_script_present():
    seams, cap = _exp_seams(enabled=True)
    adapter = R.make_research_adapter(**seams)
    run = _exp_run()
    _complete(run, R.HINT_DESIGN_EXPERIMENT, _DEFAULT_SCRIPT_REPLY)
    out = adapter(_step(run, R.HINT_RUN_EXPERIMENT), run)
    assert cap["experiment_calls"], "experiment_fn should have run"
    assert cap["experiment_calls"][0]["script"] == "print('x=1')"
    assert cap["experiment_calls"][0]["timeout_s"] == 300
    decoded = json.loads(out.text)
    assert decoded["ok"] is True
    assert decoded["result"]["stdout"] == "x=1"


def test_run_experiment_skipped_when_disabled_is_non_blocking():
    seams, cap = _exp_seams(enabled=False)
    adapter = R.make_research_adapter(**seams)
    run = _exp_run()
    _complete(run, R.HINT_DESIGN_EXPERIMENT, _DEFAULT_SCRIPT_REPLY)
    out = adapter(_step(run, R.HINT_RUN_EXPERIMENT), run)
    assert cap["experiment_calls"] == []  # the container never spawned
    decoded = json.loads(out.text)
    assert "skipped" in decoded
    assert not out.text.startswith("BLOCKED:")  # switch-off must never block a run


def test_run_experiment_skipped_when_no_script():
    seams, cap = _exp_seams(enabled=True)
    adapter = R.make_research_adapter(**seams)
    run = _exp_run()
    _complete(run, R.HINT_DESIGN_EXPERIMENT, "Sorry, I can't write that.")
    out = adapter(_step(run, R.HINT_RUN_EXPERIMENT), run)
    assert cap["experiment_calls"] == []
    assert "skipped" in json.loads(out.text)


def test_run_experiment_isolates_experiment_fn_exception():
    def boom(script, *, timeout_s=300):
        raise RuntimeError("docker down")

    seams, _ = _exp_seams(enabled=True)
    seams["experiment_fn"] = boom
    adapter = R.make_research_adapter(**seams)
    run = _exp_run()
    _complete(run, R.HINT_DESIGN_EXPERIMENT, _DEFAULT_SCRIPT_REPLY)
    out = adapter(_step(run, R.HINT_RUN_EXPERIMENT), run)
    decoded = json.loads(out.text)
    assert decoded["ok"] is False
    assert "docker down" in decoded["error"]
    assert not out.text.startswith("BLOCKED:")


def test_analyze_result_ships_and_emits_claim():
    seams, cap = _exp_seams(enabled=True, gate=None)  # gate None → ship analysis
    adapter = R.make_research_adapter(**seams)
    run = _exp_run()
    _complete(run, R.HINT_RUN_EXPERIMENT, json.dumps(_CLEAN_ENVELOPE))
    out = adapter(_step(run, R.HINT_ANALYZE_RESULT), run)
    assert not out.text.startswith("BLOCKED:")
    assert "x=1" in out.text
    assert cap["gate_calls"][0]["proposal_text"] == out.text
    assert any("claim emitted" in n for n in run.notes)


def test_analyze_result_ships_gate_final_text_on_revise():
    gate = _GateStub(action="revise", final_text="REVISED ANALYSIS")
    seams, _ = _exp_seams(enabled=True, gate=gate)
    adapter = R.make_research_adapter(**seams)
    run = _exp_run()
    _complete(run, R.HINT_RUN_EXPERIMENT, json.dumps(_CLEAN_ENVELOPE))
    out = adapter(_step(run, R.HINT_ANALYZE_RESULT), run)
    assert out.text == "REVISED ANALYSIS"
    assert not out.text.startswith("BLOCKED:")


def test_analyze_result_block_tips_to_blocked():
    gate = _GateStub(action="block", user_visible_reason="unsupported claim")
    seams, _ = _exp_seams(enabled=True, gate=gate)
    adapter = R.make_research_adapter(**seams)
    run = _exp_run()
    _complete(run, R.HINT_RUN_EXPERIMENT, json.dumps(_CLEAN_ENVELOPE))
    out = adapter(_step(run, R.HINT_ANALYZE_RESULT), run)
    assert out.text.startswith("BLOCKED:")
    assert "unsupported claim" in out.text


def test_analyze_result_handles_skipped_experiment_without_block():
    seams, _ = _exp_seams(enabled=False, gate=None)
    adapter = R.make_research_adapter(**seams)
    run = _exp_run()
    _complete(run, R.HINT_RUN_EXPERIMENT, json.dumps({"skipped": "research_experiments_enabled off"}))
    out = adapter(_step(run, R.HINT_ANALYZE_RESULT), run)
    assert not out.text.startswith("BLOCKED:")
    assert "not run" in out.text.lower()


def test_analyze_result_gate_exception_is_isolated():
    def boom(**kw):
        raise RuntimeError("gate down")

    seams, _ = _exp_seams(enabled=True)
    seams["gate_output_fn"] = boom
    adapter = R.make_research_adapter(**seams)
    run = _exp_run()
    _complete(run, R.HINT_RUN_EXPERIMENT, json.dumps(_CLEAN_ENVELOPE))
    out = adapter(_step(run, R.HINT_ANALYZE_RESULT), run)
    assert not out.text.startswith("BLOCKED:")
    assert "x=1" in out.text


# ── End-to-end drive over the experiment plan ─────────────────────────────────


def test_experiment_run_to_completion_happy_path():
    seams, cap = _exp_seams(enabled=True, gate=None)
    run = R.build_research_run("optimise retrieval", experiment=True)
    R.run_to_completion(run, adapter=R.make_research_adapter(**seams))

    assert run.status is ExecutorStatus.COMPLETED
    assert all(s.status is StepStatus.COMPLETED for s in run.plan)
    assert cap["experiment_calls"], "experiment ran autonomously"
    # the analysis carried the measurement, and the draft step ran after it
    assert "x=1" in _step(run, R.HINT_ANALYZE_RESULT).result_text
    assert _step(run, R.HINT_DRAFT).result_text == "DRAFT"


def test_experiment_run_blocks_on_gate_block():
    gate = _GateStub(action="block", user_visible_reason="claim unsupported")
    seams, _ = _exp_seams(enabled=True, gate=gate)
    run = R.build_research_run("optimise retrieval", experiment=True)
    R.run_to_completion(run, adapter=R.make_research_adapter(**seams))

    assert run.status is ExecutorStatus.BLOCKED
    assert _step(run, R.HINT_ANALYZE_RESULT).result_text.startswith("BLOCKED:")


def test_experiment_run_to_completion_skipped_still_completes():
    # Switch off → experiment skipped, but design + analysis + draft + gate
    # still run, so the run completes (autonomy is bounded, not broken).
    seams, cap = _exp_seams(enabled=False, gate=None)
    run = R.build_research_run("optimise retrieval", experiment=True)
    R.run_to_completion(run, adapter=R.make_research_adapter(**seams))

    assert run.status is ExecutorStatus.COMPLETED
    assert cap["experiment_calls"] == []
    assert "not run" in _step(run, R.HINT_ANALYZE_RESULT).result_text.lower()


# ── Analysis narrative builder ────────────────────────────────────────────────


def test_build_analysis_text_clean_run():
    run = _exp_run()
    _complete(
        run,
        R.HINT_RUN_EXPERIMENT,
        json.dumps({"ok": True, "result": {"ok": True, "returncode": 0, "stdout": "measured=9", "stderr": "", "timed_out": False}}),
    )
    text = R._build_analysis_text(run)
    assert "completion" in text.lower()
    assert "measured=9" in text


def test_build_analysis_text_failure():
    run = _exp_run()
    _complete(run, R.HINT_RUN_EXPERIMENT, json.dumps({"ok": False, "error": "container create failed (500)"}))
    text = R._build_analysis_text(run)
    assert "failed to run" in text.lower()
    assert "500" in text


def test_build_analysis_text_skipped():
    run = _exp_run()
    _complete(run, R.HINT_RUN_EXPERIMENT, json.dumps({"skipped": "no experiment script produced"}))
    text = R._build_analysis_text(run)
    assert "not run" in text.lower()
    assert "no experiment script produced" in text


def test_build_analysis_text_timed_out():
    run = _exp_run()
    _complete(
        run,
        R.HINT_RUN_EXPERIMENT,
        json.dumps({"ok": True, "result": {"ok": False, "returncode": -1, "stdout": "partial", "stderr": "", "timed_out": True}}),
    )
    text = R._build_analysis_text(run)
    assert "timed out" in text.lower()


# ── Draft prompt folds analysis when the experiment spine replaced investigate ─


def test_draft_prompt_folds_analysis_when_no_investigate():
    run = _exp_run()
    _complete(run, R.HINT_ANALYZE_RESULT, "experiment measured throughput=1200 rps")
    prompt = R._build_draft_prompt(run)
    assert "throughput=1200 rps" in prompt


# ── Package wiring ────────────────────────────────────────────────────────────


def test_experiment_modules_listed_in_package():
    import app.research as research

    assert "experiment" in research.__all__
    assert "experiment_job" in research.__all__
