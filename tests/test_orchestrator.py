"""Tests for the verified-engine orchestrator (app/self_improvement/orchestrator.py).

The evolver spawn and the change-request filer are injected, so the
spawn → gate-on-verdict → file-CR-with-evidence flow is validated on the host
without Docker or the pydantic-gated change-request lifecycle.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

try:
    from app.self_improvement.orchestrator import (
        run_verified_cycle,
        _evidence_reason,
        _maybe_self_improve_job,
        make_self_improvement_adapter,
    )
except Exception as exc:  # pragma: no cover
    pytest.skip(f"app import unavailable: {exc}", allow_module_level=True)


def _improved_result():
    return {
        "ok": True,
        "result": {
            "proposable": True,
            "verdict": {
                "verdict": "IMPROVED",
                "reason": "fixed 1 test(s)",
                "evidence": {
                    "correctness": {"fixes": ["tests/t.py::a"], "regressions": [], "delta": 1}
                },
            },
            "changed_files": ["app/crews/x.py"],
            "changed_file_contents": {"app/crews/x.py": "new source\n"},
            "diff": "+added\n-removed\n",
        },
    }


def test_improved_files_one_cr_per_file():
    calls = []

    def filer(**kw):
        calls.append(kw)
        return SimpleNamespace(id=f"cr-{len(calls)}")

    out = run_verified_cycle(
        "app/crews/x.py", "fix the bug",
        spawn=lambda job: _improved_result(), cr_filer=filer,
    )

    assert out["ok"] is True
    assert out["verdict"] == "IMPROVED"
    assert out["filed"] == ["cr-1"]
    assert len(calls) == 1
    assert calls[0]["path"] == "app/crews/x.py"
    assert calls[0]["new_content"] == "new source\n"
    assert calls[0]["requestor"] == "self_improver"
    # The CR reason carries REAL evidence, not a noise delta.
    assert "Verified self-improvement" in calls[0]["reason"]
    assert "IMPROVED" in calls[0]["reason"]
    assert "fixed 1 test" in calls[0]["reason"]


def test_not_proposable_files_nothing():
    res = {"ok": True, "result": {"proposable": False, "verdict": {"verdict": "NO_CHANGE"}}}
    out = run_verified_cycle(
        "app/crews/x.py", "x", spawn=lambda job: res, cr_filer=lambda **k: 1 / 0
    )
    assert out["ok"] is True
    assert out["verdict"] == "NO_CHANGE"
    assert out["filed"] == []


def test_evolver_failure_surfaces_error():
    out = run_verified_cycle(
        "app/crews/x.py", "x",
        spawn=lambda job: {"ok": False, "error": "container boom"},
        cr_filer=lambda **k: None,
    )
    assert out["ok"] is False
    assert "container boom" in out["error"]


def test_proposable_but_no_changed_files():
    res = {
        "ok": True,
        "result": {"proposable": True, "verdict": {"verdict": "INVARIANTS_ONLY"}, "changed_file_contents": {}},
    }
    out = run_verified_cycle("app/crews/x.py", "x", spawn=lambda job: res, cr_filer=lambda **k: None)
    assert out["filed"] == []
    assert "no changed files" in out["note"]


def test_job_carries_normalized_target_and_budget():
    seen = {}

    def spawn(job):
        seen.update(job)
        return {"ok": True, "result": {"proposable": False, "verdict": {"verdict": "NO_CHANGE"}}}

    run_verified_cycle("crews/x.py", "do y", budget_usd=3.0, spawn=spawn, cr_filer=lambda **k: None)
    assert seen["target_file"] == "app/crews/x.py"  # normalized
    assert seen["approach"] == "do y"
    assert seen["budget_usd"] == 3.0


def test_maybe_self_improve_job_detects_json_job():
    assert _maybe_self_improve_job('{"target_file": "app/x.py", "approach": "y"}') == {
        "target_file": "app/x.py",
        "approach": "y",
    }


def test_maybe_self_improve_job_rejects_non_job():
    assert _maybe_self_improve_job("just research the topic") is None
    assert _maybe_self_improve_job('{"goal": "no target_file here"}') is None


def test_adapter_routes_non_job_steps_to_base():
    # A normal (non-self-improve) step must fall through to the base adapter
    # untouched — the verified engine only claims JSON jobs with a target_file.
    base_calls = []

    def base(step, run):
        base_calls.append(step.description)
        return "BASE_RESULT"

    adapter = make_self_improvement_adapter(default_adapter=base)
    step = SimpleNamespace(description="research Finnish forests")
    run = SimpleNamespace(requestor="operator")
    assert adapter(step, run) == "BASE_RESULT"
    assert base_calls == ["research Finnish forests"]


def test_evidence_reason_includes_quality_and_diff():
    verdict = {
        "verdict": "IMPROVED",
        "reason": "benchmark up",
        "evidence": {"quality": {"mean_delta": 0.2, "wins": 5, "losses": 0, "samples": 6}},
    }
    result = {"diff": "+a\n+b\n-c\n", "changed_files": ["app/x.py"]}
    reason = _evidence_reason("app/x.py", "improve", verdict, result)
    assert "Quality: Δ=0.2" in reason
    assert "+2/-1 lines" in reason
    assert "public API preserved" in reason
