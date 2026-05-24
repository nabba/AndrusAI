"""Gap 5 — model_swap_validator tests.

Covers:
  * No regression → ok=True
  * One tier regresses → ok=False + per-task delta surfaces
  * Both cascades fail → ok=False + errors populated
  * Budget cap is honored
  * Per-tier summary aggregates mean + max_regression correctly
  * SwapValidationResult.to_dict serializes cleanly
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("pydantic")


@pytest.fixture
def fake_catalog(monkeypatch):
    """Tiny in-memory catalog of 3 tasks, each scored against
    cheap+default+smart tiers."""
    from app.benchmarks.models import BenchmarkTask

    tasks = [
        BenchmarkTask(
            id="t1",
            input="say hi",
            expected="hi",
            scorer="exact_match",
            scorer_args={},
            model_targets=["cheap", "default"],
            max_tokens=32,
            timeout_s=10,
        ),
        BenchmarkTask(
            id="t2",
            input="add 2+2",
            expected="4",
            scorer="contains",
            scorer_args={"substrings": ["4"]},
            model_targets=["default", "smart"],
            max_tokens=32,
            timeout_s=10,
        ),
        BenchmarkTask(
            id="t3",
            input="summarize x",
            expected="summary",
            scorer="contains",
            scorer_args={"substrings": ["summary"]},
            model_targets=["smart"],
            max_tokens=128,
            timeout_s=10,
        ),
    ]
    monkeypatch.setattr(
        "app.benchmarks.catalog.load_catalog", lambda: iter(tasks)
    )
    return tasks


def _stub_llm_call(per_tier_score: dict[str, float]):
    """Build an LLMCall stub that returns canned outputs scoring at
    per_tier_score[tier] when fed to the benchmark scorers.

    The stub returns expected output verbatim when score>=1.0,
    a wrong output when score<0.5, and a partial-match output for
    in-between scores via the 'contains' scorer.
    """
    from app.benchmarks.models import LLMResult

    def _llm(*, prompt: str, model_tier: str, max_tokens: int, timeout_s: int):
        target = per_tier_score.get(model_tier, 1.0)
        # Trivially produce the right output for ≥1.0, wrong for <0.5
        if target >= 0.99:
            # All scorers in the fake catalog accept the right answer
            if "say hi" in prompt:
                return LLMResult(output="hi", cost_usd=0.001)
            if "2+2" in prompt:
                return LLMResult(output="The answer is 4", cost_usd=0.001)
            if "summarize" in prompt:
                return LLMResult(output="This is a summary", cost_usd=0.001)
        else:
            return LLMResult(output="garbage", cost_usd=0.001)
        return LLMResult(output="garbage", cost_usd=0.001)

    return _llm


def test_no_regression_returns_ok(fake_catalog, monkeypatch):
    from app.llm import model_swap_validator as msv

    old = _stub_llm_call({"cheap": 1.0, "default": 1.0, "smart": 1.0})
    new = _stub_llm_call({"cheap": 1.0, "default": 1.0, "smart": 1.0})
    result = msv.validate_cascade_change(
        old_llm_call=old,
        new_llm_call=new,
        old_label="A",
        new_label="B",
        tiers=["cheap", "default", "smart"],
    )
    assert result.ok is True
    assert result.regressed_tasks() == []
    assert result.old_label == "A"
    assert result.new_label == "B"


def test_one_tier_regresses_returns_not_ok(fake_catalog, monkeypatch):
    """When default-tier scores drop from 1.0 to 0.0, the validator
    must flag every default-tier task as regressed."""
    from app.llm import model_swap_validator as msv

    old = _stub_llm_call({"cheap": 1.0, "default": 1.0, "smart": 1.0})
    new = _stub_llm_call({"cheap": 1.0, "default": 0.0, "smart": 1.0})
    result = msv.validate_cascade_change(
        old_llm_call=old,
        new_llm_call=new,
        tiers=["cheap", "default", "smart"],
    )
    assert result.ok is False
    regressed = result.regressed_tasks()
    assert len(regressed) >= 1
    assert all(d.tier == "default" for d in regressed)
    summary = result.per_tier_summary["default"]
    assert summary["mean_new"] < summary["mean_old"]


def test_budget_cap_stops_catalog(fake_catalog, monkeypatch):
    """When the cost cap is below per-task cost × num_tasks, the
    validator stops mid-catalog and records the cap in errors."""
    from app.llm import model_swap_validator as msv

    old = _stub_llm_call({"cheap": 1.0, "default": 1.0, "smart": 1.0})
    new = _stub_llm_call({"cheap": 1.0, "default": 1.0, "smart": 1.0})
    result = msv.validate_cascade_change(
        old_llm_call=old,
        new_llm_call=new,
        tiers=["cheap", "default", "smart"],
        budget_usd=0.0005,  # below per-task cost
    )
    assert any("budget cap reached" in e for e in result.errors)


def test_regression_threshold_constant():
    """REGRESSION_THRESHOLD is the operator-facing tunable."""
    from app.llm import model_swap_validator as msv

    assert 0.0 < msv.REGRESSION_THRESHOLD < 1.0


def test_per_tier_summary_aggregates(fake_catalog, monkeypatch):
    from app.llm import model_swap_validator as msv

    old = _stub_llm_call({"cheap": 1.0, "default": 1.0, "smart": 1.0})
    new = _stub_llm_call({"cheap": 0.0, "default": 1.0, "smart": 0.0})
    result = msv.validate_cascade_change(
        old_llm_call=old, new_llm_call=new,
        tiers=["cheap", "default", "smart"],
    )
    cheap = result.per_tier_summary["cheap"]
    assert cheap["n_tasks"] > 0
    assert cheap["max_regression"] <= -0.99


def test_to_dict_serializes(fake_catalog, monkeypatch):
    from app.llm import model_swap_validator as msv

    old = _stub_llm_call({"cheap": 1.0, "default": 1.0, "smart": 1.0})
    new = _stub_llm_call({"cheap": 1.0, "default": 1.0, "smart": 1.0})
    result = msv.validate_cascade_change(
        old_llm_call=old, new_llm_call=new,
        tiers=["cheap"],
    )
    d = result.to_dict()
    import json

    blob = json.dumps(d)
    assert "ok" in blob
    assert "per_task" in blob
    assert "per_tier_summary" in blob


def test_catalog_unavailable_records_error(monkeypatch):
    """When the benchmark catalog can't be loaded, the validator
    returns ok=False + an error rather than raising."""
    from app.llm import model_swap_validator as msv

    monkeypatch.setattr(
        "app.benchmarks.catalog.load_catalog",
        lambda: (_ for _ in ()).throw(RuntimeError("no yaml")),
    )
    result = msv.validate_cascade_change(
        old_llm_call=lambda **kw: None,
        new_llm_call=lambda **kw: None,
        tiers=["cheap"],
    )
    assert result.ok is False
    assert any("catalog load failed" in e for e in result.errors)


def test_zero_regression_when_models_score_identically(fake_catalog, monkeypatch):
    """Sanity: identical cascades produce zero delta + zero regressions."""
    from app.llm import model_swap_validator as msv

    same = _stub_llm_call({"cheap": 1.0, "default": 1.0, "smart": 1.0})
    result = msv.validate_cascade_change(
        old_llm_call=same, new_llm_call=same, tiers=["default"]
    )
    assert result.ok is True
    assert all(d.delta == 0.0 for d in result.per_task)


def test_main_cli_exits_nonzero_on_regression(monkeypatch, fake_catalog, capsys):
    """CLI exit code is 0 on PASS, 1 on FAIL — needed for CI gating."""
    from app.llm import model_swap_validator as msv

    # Bypass the resolver — return our stubs directly via the module
    # function under test.
    real_validate = msv.validate_cascade_change

    def _stub_validate(**kw):
        result = real_validate(
            old_llm_call=_stub_llm_call({"cheap": 1.0, "default": 1.0, "smart": 1.0}),
            new_llm_call=_stub_llm_call({"cheap": 0.0, "default": 0.0, "smart": 0.0}),
            old_label=kw.get("old_label", "old"),
            new_label=kw.get("new_label", "new"),
            tiers=kw.get("tiers"),
            budget_usd=kw.get("budget_usd"),
        )
        return result

    monkeypatch.setattr(msv, "validate_cascade_change", _stub_validate)
    rc = msv.main([
        "--old", "default=A",
        "--new", "default=B",
        "--tier", "default",
    ])
    assert rc == 1
