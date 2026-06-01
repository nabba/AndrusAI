"""Host-safe tests for app.research.experiment_repair (the bounded
design→run→repair loop) and its wiring into app.research.run's run_experiment
step.

The loop reuses app.coding_session.iterate.iterate_until_green verbatim; every
external call (the container runner ``experiment_fn``, the repair completion
``repair_fn``, the script extractor ``extract_fn``) is injected, so these tests
run with no Docker, no LLM, no crewai. ``extract_fn`` is the identity here so a
"repair reply" IS the new script — the fenced-block extraction is covered by
test_research_experiment.py.
"""

from __future__ import annotations

import json

import app.research.run as R
from app.research import experiment_repair as ER
from app.autonomous_executor.driver import CommanderResult
from app.autonomous_executor.models import ExecutorRun, ExecutorStatus, StepStatus


# ── Envelope builders (the shape run_experiment_script returns) ───────────────


def _clean(stdout="answer=42"):
    return {"ok": True, "result": {"ok": True, "returncode": 0, "stdout": stdout, "stderr": "", "timed_out": False}}


def _failed(stderr="NameError: x"):
    return {"ok": True, "result": {"ok": False, "returncode": 1, "stdout": "", "stderr": stderr, "timed_out": False}}


def _empty_stdout():
    return {"ok": True, "result": {"ok": True, "returncode": 0, "stdout": "   ", "stderr": "", "timed_out": False}}


def _timed_out():
    return {"ok": True, "result": {"ok": False, "returncode": -1, "stdout": "", "stderr": "", "timed_out": True}}


def _transport_fail():
    return {"ok": False, "error": "container create failed (500)"}


def _seq_experiment_fn(*envelopes):
    """Return an experiment_fn that yields the given envelopes in order (the
    last one repeats once exhausted), recording every script it ran."""
    calls: dict = {"scripts": []}
    seq = list(envelopes)

    def fn(script, *, timeout_s=300):
        calls["scripts"].append(script)
        return seq.pop(0) if len(seq) > 1 else seq[0]

    return fn, calls


_ID = lambda reply: reply  # identity extractor: a repair reply is the new script  # noqa: E731


# ── _measurement_present / _error_text ───────────────────────────────────────


def test_measurement_present_truth_table():
    assert ER._measurement_present(_clean()) is True
    assert ER._measurement_present(_failed()) is False
    assert ER._measurement_present(_empty_stdout()) is False  # clean exit, no output ≠ measurement
    assert ER._measurement_present(_transport_fail()) is False
    assert ER._measurement_present("not a dict") is False
    assert ER._measurement_present({"ok": True}) is False  # no result block


def test_error_text_describes_each_failure():
    assert "create failed" in ER._error_text(_transport_fail())
    assert "NameError" in ER._error_text(_failed("NameError: x"))
    assert "timed out" in ER._error_text(_timed_out()).lower()
    assert "no measurement" in ER._error_text(_empty_stdout()).lower()


# ── run_experiment_with_repair ────────────────────────────────────────────────


def test_clean_first_run_skips_repair():
    fn, calls = _seq_experiment_fn(_clean("answer=42"))
    repair_calls = []
    out = ER.run_experiment_with_repair(
        "print('answer=42')",
        experiment_fn=fn,
        extract_fn=_ID,
        repair_fn=lambda p: repair_calls.append(p) or "should-not-be-used",
    )
    assert out["ok"] is True
    assert out["result"]["stdout"] == "answer=42"
    assert out["repair"] == {"status": "passed", "rounds": 0, "fixes": 0}
    assert calls["scripts"] == ["print('answer=42')"]  # ran exactly once
    assert repair_calls == []  # a clean run never pays for a repair


def test_repairs_failing_script_then_passes():
    fn, calls = _seq_experiment_fn(_failed("NameError"), _clean("ok=1"))
    out = ER.run_experiment_with_repair(
        "prnit('ok=1')",  # typo'd original
        experiment_fn=fn,
        extract_fn=_ID,
        repair_fn=lambda prompt: "print('ok=1')",  # the fix
    )
    assert out["ok"] is True
    assert out["result"]["stdout"] == "ok=1"
    assert out["repair"]["status"] == "passed"
    assert out["repair"]["rounds"] == 1
    assert out["repair"]["fixes"] == 1
    # the SECOND container run got the repaired script
    assert calls["scripts"] == ["prnit('ok=1')", "print('ok=1')"]


def test_empty_stdout_is_repaired():
    fn, _ = _seq_experiment_fn(_empty_stdout(), _clean("measured=5"))
    out = ER.run_experiment_with_repair(
        "x = 1  # forgot to print",
        experiment_fn=fn,
        extract_fn=_ID,
        repair_fn=lambda p: "print('measured=5')",
    )
    assert out["ok"] is True
    assert out["result"]["stdout"] == "measured=5"
    assert out["repair"]["status"] == "passed"


def test_gives_up_when_repair_returns_no_new_script():
    fn, calls = _seq_experiment_fn(_failed("boom"))
    out = ER.run_experiment_with_repair(
        "broken",
        experiment_fn=fn,
        extract_fn=_ID,
        repair_fn=lambda prompt: "broken",  # same script back → declined
    )
    assert out["ok"] is True  # transport ok, but the script failed
    assert out["result"]["ok"] is False
    assert out["repair"]["status"] == "no_fix_available"
    assert out["repair"]["fixes"] == 0
    assert calls["scripts"] == ["broken"]  # ran once, no pointless re-run


def test_max_rounds_bounds_the_loop():
    # Always fails; repair always offers a *different* script, so only the round
    # cap stops it. max_rounds repairs are each re-tested → max_rounds+1 runs.
    fn, calls = _seq_experiment_fn(_failed("boom"))
    counter = {"n": 0}

    def repair_fn(prompt):
        counter["n"] += 1
        return f"print('attempt {counter['n']}')"

    out = ER.run_experiment_with_repair(
        "v0", experiment_fn=fn, extract_fn=_ID, repair_fn=repair_fn, max_rounds=2
    )
    assert out["repair"]["status"] == "max_iterations"
    assert len(calls["scripts"]) == 3  # initial + 2 repairs, each re-tested


def test_experiment_fn_raising_is_isolated():
    def boom(script, *, timeout_s=300):
        raise RuntimeError("docker down")

    out = ER.run_experiment_with_repair(
        "print(1)", experiment_fn=boom, extract_fn=_ID, repair_fn=lambda p: "print(2)"
    )
    assert out["ok"] is False
    assert "never ran" in out["error"]
    assert out["repair"]["status"] == "test_runner_error"


def test_transport_failure_is_retried():
    fn, _ = _seq_experiment_fn(_transport_fail(), _clean("ok"))
    out = ER.run_experiment_with_repair(
        "print('ok')", experiment_fn=fn, extract_fn=_ID, repair_fn=lambda p: "print('ok')  # retried"
    )
    assert out["ok"] is True
    assert out["repair"]["status"] == "passed"


def test_repair_fn_exception_stops_cleanly():
    fn, _ = _seq_experiment_fn(_failed("boom"))

    def boom_repair(prompt):
        raise RuntimeError("llm down")

    out = ER.run_experiment_with_repair(
        "broken", experiment_fn=fn, extract_fn=_ID, repair_fn=boom_repair
    )
    assert out["repair"]["status"] == "no_fix_available"
    assert out["ok"] is True and out["result"]["ok"] is False  # last (failing) envelope returned


# ── run.py integration: the run_experiment branch routes through repair ───────


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


def _adapter(*, repair_enabled, repair_spy):
    return R.make_research_adapter(
        search_fn=lambda g: [],
        propose_fn=lambda q, **k: [],
        commander_fn=lambda s, r: CommanderResult(text="FALLBACK"),
        gate_fn=lambda **k: (None, ""),
        experiment_fn=lambda script, *, timeout_s=300: _clean("one-shot"),
        enabled_fn=lambda: True,
        repair_enabled_fn=lambda: repair_enabled,
        experiment_repair_fn=repair_spy,
        gate_output_fn=lambda **k: None,
        design_fn=lambda p: "```python\nprint('x=1')\n```",
        draft_fn=lambda p: "DRAFT",
    )


def test_run_experiment_routes_through_repair_when_enabled():
    seen = []

    def repair_spy(script, *, experiment_fn, goal, timeout_s=300):
        seen.append({"script": script, "goal": goal})
        return {**_clean("repaired=1"), "repair": {"status": "passed", "rounds": 1, "fixes": 1}}

    adapter = _adapter(repair_enabled=True, repair_spy=repair_spy)
    run = _exp_run()
    _complete(run, R.HINT_DESIGN_EXPERIMENT, "```python\nprint('x=1')\n```")
    out = adapter(_step(run, R.HINT_RUN_EXPERIMENT), run)

    assert seen and seen[0]["script"] == "print('x=1')"  # extracted script handed to repair loop
    assert seen[0]["goal"] == "optimise retrieval"
    assert json.loads(out.text)["result"]["stdout"] == "repaired=1"
    assert any("ran with repair" in n for n in run.notes)


def test_run_experiment_one_shot_when_repair_disabled():
    seen = []
    adapter = _adapter(repair_enabled=False, repair_spy=lambda *a, **k: seen.append(1) or _clean())
    run = _exp_run()
    _complete(run, R.HINT_DESIGN_EXPERIMENT, "```python\nprint('x=1')\n```")
    out = adapter(_step(run, R.HINT_RUN_EXPERIMENT), run)

    assert seen == []  # repair seam untouched when the switch is off
    assert json.loads(out.text)["result"]["stdout"] == "one-shot"
    assert any("experiment: ran (ok=" in n for n in run.notes)  # the one-shot note, not the repair note
