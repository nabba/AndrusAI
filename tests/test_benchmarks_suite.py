"""Tests for the benchmark suite (Phase C.3, 2026-05-22).

Comprehensive coverage of:
  * scorers — pure-function contracts
  * store — JSONL round-trip + tolerance to malformed rows
  * catalog — YAML loader + validation + dedup
  * runner — single-task + multi-task + cost cap
  * aggregator — filter + percentile + group-by views
  * scheduler_job — cadence + master switch + cost cap (the
    pydantic_settings-gated ones skip on host)

Conftest.py installs psycopg2 + crewai stubs; we add a yaml stub fallback
in case pyyaml is missing on the host.
"""
from __future__ import annotations

import json
import sys
import textwrap
import time
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest


_mock_psycopg2 = MagicMock()
_mock_psycopg2.InterfaceError = type("InterfaceError", (Exception,), {})
_mock_psycopg2.OperationalError = type("OperationalError", (Exception,), {})
sys.modules.setdefault("psycopg2", _mock_psycopg2)
sys.modules.setdefault("psycopg2.pool", MagicMock())


# ── Module imports under test ───────────────────────────────────────


from app.benchmarks import (  # noqa: E402
    BenchmarkRun,
    BenchmarkTask,
    LLMResult,
    filter_runs,
    leaderboard,
    per_model,
    per_task,
    per_task_and_model,
    score,
    summarise,
)
from app.benchmarks import scorers as benchmark_scorers  # noqa: E402
from app.benchmarks import store as benchmark_store  # noqa: E402
from app.benchmarks import catalog as benchmark_catalog  # noqa: E402
from app.benchmarks import runner as benchmark_runner  # noqa: E402


# ============================================================================
# Scorers
# ============================================================================


class TestScorers:
    def test_exact_match_happy(self):
        assert benchmark_scorers.exact_match("foo", "foo") == 1.0
        assert benchmark_scorers.exact_match("foo", "bar") == 0.0

    def test_exact_match_strip_default(self):
        # Default strip=True
        assert benchmark_scorers.exact_match(" foo ", "foo") == 1.0

    def test_exact_match_case_folding(self):
        assert (
            benchmark_scorers.exact_match(
                "FOO", "foo", case_sensitive=False,
            ) == 1.0
        )
        assert benchmark_scorers.exact_match("FOO", "foo") == 0.0

    def test_exact_match_non_string(self):
        assert benchmark_scorers.exact_match(None, "foo") == 0.0
        assert benchmark_scorers.exact_match("foo", 42) == 0.0

    def test_contains_all_required(self):
        # 3 of 4 → 0.75
        r = benchmark_scorers.contains(
            "the quick brown fox",
            ["quick", "brown", "fox", "jumps"],
            case_sensitive=False,
        )
        assert r == 0.75

    def test_contains_any_acceptable(self):
        r = benchmark_scorers.contains(
            "the quick brown fox",
            ["zebra", "fox"],
            all_required=False,
        )
        assert r == 1.0
        r = benchmark_scorers.contains(
            "the quick brown fox",
            ["zebra", "buffalo"],
            all_required=False,
        )
        assert r == 0.0

    def test_contains_empty_expected(self):
        assert benchmark_scorers.contains("anything", []) == 0.0

    def test_regex_match_happy(self):
        assert benchmark_scorers.regex_match("abc123", r"\d+") == 1.0
        assert benchmark_scorers.regex_match("abc", r"\d+") == 0.0

    def test_regex_match_malformed_returns_zero(self):
        # Unmatched bracket — malformed regex; must NOT raise
        assert benchmark_scorers.regex_match("foo", "[unclosed") == 0.0

    def test_json_keys_present_happy(self):
        out = '{"name": "Alice", "age": 30, "city": "Helsinki"}'
        r = benchmark_scorers.json_keys_present(
            out, ["name", "age", "city"],
        )
        assert r == 1.0

    def test_json_keys_present_partial(self):
        out = '{"name": "Alice", "age": 30}'
        r = benchmark_scorers.json_keys_present(
            out, ["name", "age", "city", "country"],
        )
        assert r == 0.5  # 2 of 4

    def test_json_keys_present_code_fence_stripped(self):
        out = '```json\n{"name": "Alice"}\n```'
        r = benchmark_scorers.json_keys_present(out, ["name"])
        assert r == 1.0

    def test_json_keys_present_non_json(self):
        assert (
            benchmark_scorers.json_keys_present("not json", ["k"]) == 0.0
        )

    def test_json_keys_present_array_top_level(self):
        # Top-level is a list, not a dict
        assert benchmark_scorers.json_keys_present("[1,2]", ["k"]) == 0.0

    def test_length_within_happy(self):
        assert benchmark_scorers.length_within("hello", {"min": 3, "max": 10}) == 1.0
        assert benchmark_scorers.length_within("hi", {"min": 3, "max": 10}) == 0.0
        assert benchmark_scorers.length_within("x" * 20, {"max": 10}) == 0.0

    def test_score_dispatcher_unknown(self):
        # Unknown scorer never raises — returns 0.0
        assert score("nonexistent_scorer", "out", "exp") == 0.0

    def test_score_dispatcher_clamps_out_of_range(self):
        # Custom scorer returning 2.0 should clamp to 1.0
        original = benchmark_scorers.SCORER_REGISTRY.copy()
        try:
            benchmark_scorers.SCORER_REGISTRY["fake"] = (
                lambda out, exp: 2.0  # noqa: ARG005
            )
            assert score("fake", "x", "y") == 1.0
        finally:
            benchmark_scorers.SCORER_REGISTRY.clear()
            benchmark_scorers.SCORER_REGISTRY.update(original)


# ============================================================================
# Models
# ============================================================================


class TestModels:
    def test_benchmark_task_validation_empty_id(self):
        with pytest.raises(ValueError):
            BenchmarkTask(
                id="", name="x", description="", input="i",
                expected="x", scorer="exact_match",
            )

    def test_benchmark_task_validation_empty_input(self):
        with pytest.raises(ValueError):
            BenchmarkTask(
                id="x", name="x", description="", input="",
                expected="x", scorer="exact_match",
            )

    def test_benchmark_task_validation_empty_scorer(self):
        with pytest.raises(ValueError):
            BenchmarkTask(
                id="x", name="x", description="", input="i",
                expected="x", scorer="",
            )

    def test_benchmark_task_validation_empty_targets(self):
        with pytest.raises(ValueError):
            BenchmarkTask(
                id="x", name="x", description="", input="i",
                expected="x", scorer="exact_match",
                model_targets=[],
            )

    def test_benchmark_task_default_targets(self):
        t = BenchmarkTask(
            id="x", name="x", description="", input="i",
            expected="x", scorer="exact_match",
        )
        assert t.model_targets == ["default"]

    def test_benchmark_run_passed_strict(self):
        r = BenchmarkRun(
            task_id="t", model="m", ts="2026-05-22T10:00:00+00:00",
            score=0.9, latency_ms=100,
            tokens_in=0, tokens_out=0, cost_usd=0.0,
            output_preview="",
        )
        # 0.9 is partial credit, not pass
        assert r.passed is False

        r2 = BenchmarkRun(
            task_id="t", model="m", ts="2026-05-22T10:00:00+00:00",
            score=1.0, latency_ms=100,
            tokens_in=0, tokens_out=0, cost_usd=0.0,
            output_preview="",
        )
        assert r2.passed is True

    def test_benchmark_run_json_roundtrip(self):
        r = BenchmarkRun(
            task_id="t", model="m", ts="2026-05-22T10:00:00+00:00",
            score=0.5, latency_ms=42,
            tokens_in=10, tokens_out=20, cost_usd=0.001,
            output_preview="hello",
        )
        line = r.to_json_line()
        d = json.loads(line)
        assert d["task_id"] == "t"
        # Round-trip
        r2 = BenchmarkRun.from_dict(d)
        assert r2 == r

    def test_benchmark_run_from_dict_tolerates_missing_optional(self):
        # Minimal dict — only required keys
        r = BenchmarkRun.from_dict({
            "task_id": "t", "model": "m",
            "ts": "2026-05-22T10:00:00+00:00",
            "score": 0.5,
        })
        assert r.latency_ms == 0
        assert r.error == ""


# ============================================================================
# Store
# ============================================================================


class TestStore:
    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path):
        benchmark_store.reset_for_tests(tmp_path / "bench")
        yield
        benchmark_store.reset_for_tests(None)

    def test_append_and_read_roundtrip(self):
        run = BenchmarkRun(
            task_id="t", model="m",
            ts="2026-05-22T10:00:00+00:00",
            score=1.0, latency_ms=100,
            tokens_in=0, tokens_out=0, cost_usd=0.0,
            output_preview="ok",
        )
        benchmark_store.append_run(run)
        got = benchmark_store.load_all()
        assert len(got) == 1
        assert got[0] == run

    def test_malformed_line_skipped(self, tmp_path):
        # Hand-write a JSONL with one good and one bad line
        path = benchmark_store.runs_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        good = BenchmarkRun(
            task_id="t", model="m",
            ts="2026-05-22T10:00:00+00:00",
            score=1.0, latency_ms=10,
            tokens_in=0, tokens_out=0, cost_usd=0.0,
            output_preview="",
        )
        with path.open("w", encoding="utf-8") as fp:
            fp.write(good.to_json_line() + "\n")
            fp.write("not json {\n")  # malformed
            fp.write("\n")  # blank
            fp.write('{"task_id": null}\n')  # unrehydratable
            fp.write(good.to_json_line() + "\n")
        got = benchmark_store.load_all()
        # Two good rows, others skipped
        assert len(got) == 2

    def test_stats_empty(self):
        s = benchmark_store.stats()
        assert s["rows"] == 0
        assert s["bytes"] == 0
        assert s["last_ts"] == ""

    def test_stats_after_appends(self):
        for i in range(3):
            benchmark_store.append_run(BenchmarkRun(
                task_id=f"t{i}", model="m",
                ts=f"2026-05-22T10:0{i}:00+00:00",
                score=1.0, latency_ms=0,
                tokens_in=0, tokens_out=0, cost_usd=0.0,
                output_preview="",
            ))
        s = benchmark_store.stats()
        assert s["rows"] == 3
        assert s["bytes"] > 0
        assert s["last_ts"] == "2026-05-22T10:02:00+00:00"


# ============================================================================
# Catalog
# ============================================================================


class TestCatalog:
    def test_load_real_catalog(self):
        # The shipped YAML files load with no warnings about unknown
        # scorers / bad tiers — every task should be valid.
        tasks = benchmark_catalog.load_tasks()
        assert len(tasks) >= 10
        # Every task has a known scorer
        for t in tasks:
            assert t.scorer in benchmark_scorers.SCORER_REGISTRY

    def test_load_custom_dir_with_invalid_scorer(self, tmp_path):
        # YAML with an unknown scorer is dropped, not fatal
        bad = tmp_path / "tasks"
        bad.mkdir()
        (bad / "bad.yaml").write_text(textwrap.dedent("""
            id: bad_task
            name: Bad
            description: ""
            input: hi
            expected: ok
            scorer: this_does_not_exist
            model_targets: [cheap]
        """), encoding="utf-8")
        tasks = benchmark_catalog.load_tasks(tasks_dir=bad)
        assert tasks == []

    def test_load_custom_dir_with_unknown_tier(self, tmp_path):
        bad = tmp_path / "tasks"
        bad.mkdir()
        (bad / "tier.yaml").write_text(textwrap.dedent("""
            id: t1
            name: T
            description: ""
            input: hi
            expected: ok
            scorer: exact_match
            model_targets: [supersmart]
        """), encoding="utf-8")
        tasks = benchmark_catalog.load_tasks(tasks_dir=bad)
        assert tasks == []

    def test_load_dedupes_within_files(self, tmp_path):
        d = tmp_path / "tasks"
        d.mkdir()
        (d / "a.yaml").write_text(textwrap.dedent("""
            tasks:
              - id: t1
                name: First
                description: ""
                input: foo
                expected: foo
                scorer: exact_match
                model_targets: [cheap]
              - id: t1
                name: Second (duplicate id, should be skipped)
                description: ""
                input: bar
                expected: bar
                scorer: exact_match
                model_targets: [cheap]
        """), encoding="utf-8")
        tasks = benchmark_catalog.load_tasks(tasks_dir=d)
        assert len(tasks) == 1
        assert tasks[0].name == "First"

    def test_load_handles_empty_directory(self, tmp_path):
        empty = tmp_path / "tasks"
        empty.mkdir()
        assert benchmark_catalog.load_tasks(tasks_dir=empty) == []

    def test_load_handles_missing_directory(self, tmp_path):
        # Path that doesn't exist
        missing = tmp_path / "nope"
        assert benchmark_catalog.load_tasks(tasks_dir=missing) == []

    def test_get_task_lookup(self):
        # One of our shipped task IDs
        t = benchmark_catalog.get_task("arith_basic_addition")
        assert t is not None
        assert t.scorer == "exact_match"
        # Nonexistent
        assert benchmark_catalog.get_task("ghost_task_xyz") is None

    def test_catalog_stats_shape(self):
        s = benchmark_catalog.catalog_stats()
        assert "task_count" in s
        assert "by_category" in s
        assert "by_scorer" in s
        assert s["task_count"] >= 10


# ============================================================================
# Runner
# ============================================================================


def _make_stub_llm(output: str = "", **kwargs) -> "benchmark_runner.LLMCall":
    """Build a stub LLMCall that returns ``output`` (or error/etc)."""

    def _stub(*, prompt, model_tier, max_tokens, timeout_s):  # noqa: ARG001
        return LLMResult(output=output, **kwargs)

    return _stub


class TestRunner:
    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path):
        benchmark_store.reset_for_tests(tmp_path / "bench")
        yield
        benchmark_store.reset_for_tests(None)

    def _make_task(self, **overrides) -> BenchmarkTask:
        base = dict(
            id="t1", name="t1", description="",
            input="What is 2+2?", expected="4",
            scorer="exact_match",
            scorer_args={"case_sensitive": False, "strip": True},
            model_targets=["default"],
        )
        base.update(overrides)
        return BenchmarkTask(**base)

    def test_run_task_happy(self):
        task = self._make_task()
        run = benchmark_runner.run_task(
            task,
            model_tier="default",
            llm_call=_make_stub_llm(output="4"),
        )
        assert run.score == 1.0
        assert run.passed is True
        assert run.task_id == "t1"
        assert run.model == "default"
        assert run.error == ""

    def test_run_task_wrong_answer(self):
        task = self._make_task()
        run = benchmark_runner.run_task(
            task,
            model_tier="default",
            llm_call=_make_stub_llm(output="5"),
        )
        assert run.score == 0.0
        assert run.passed is False

    def test_run_task_llm_error_isolated(self):
        task = self._make_task()
        run = benchmark_runner.run_task(
            task,
            model_tier="default",
            llm_call=_make_stub_llm(output="", error="API down"),
        )
        assert run.score == 0.0
        assert run.passed is False
        assert "API down" in run.error

    def test_run_task_llm_raises_isolated(self):
        task = self._make_task()

        def _raising(*, prompt, model_tier, max_tokens, timeout_s):
            raise RuntimeError("boom")

        run = benchmark_runner.run_task(
            task, model_tier="default", llm_call=_raising,
        )
        assert run.score == 0.0
        assert "RuntimeError" in run.error
        assert "boom" in run.error

    def test_run_task_wrong_return_shape(self):
        task = self._make_task()

        def _bad_shape(*, prompt, model_tier, max_tokens, timeout_s):
            return "i should be an LLMResult"

        run = benchmark_runner.run_task(
            task, model_tier="default", llm_call=_bad_shape,
        )
        assert run.score == 0.0
        assert "LLMResult" in run.error

    def test_run_task_records_output_preview(self):
        task = self._make_task()
        long_output = "x" * 500
        run = benchmark_runner.run_task(
            task,
            model_tier="default",
            llm_call=_make_stub_llm(output=long_output),
        )
        # Truncated to 200 chars + ellipsis
        assert len(run.output_preview) <= 205
        assert run.output_preview.endswith("…")

    def test_run_catalog_persists_and_caps_cost(self):
        task1 = self._make_task(id="t1")
        task2 = self._make_task(id="t2")

        runs = benchmark_runner.run_catalog(
            [task1, task2],
            llm_call=_make_stub_llm(output="4", cost_usd=1.50),
            persist=True,
            max_cost_usd=1.00,  # cap stops after first run
        )
        # Only the first task ran — cap kicked in for the second
        assert len(runs) == 1
        assert runs[0].task_id == "t1"

        # Verify persistence
        stored = benchmark_store.load_all()
        assert len(stored) == 1

    def test_run_task_against_all_targets(self):
        task = self._make_task(
            model_targets=["cheap", "default", "smart"],
        )
        runs = benchmark_runner.run_task_against_all_targets(
            task,
            llm_call=_make_stub_llm(output="4"),
        )
        assert len(runs) == 3
        assert {r.model for r in runs} == {"cheap", "default", "smart"}


# ============================================================================
# Aggregator
# ============================================================================


def _r(
    task: str, model: str, score: float,
    ts: str = "2026-05-22T10:00:00+00:00",
    latency_ms: int = 100, cost: float = 0.001,
    error: str = "",
) -> BenchmarkRun:
    return BenchmarkRun(
        task_id=task, model=model, ts=ts, score=score,
        latency_ms=latency_ms, tokens_in=10, tokens_out=20,
        cost_usd=cost, output_preview="", error=error,
    )


class TestAggregator:
    def test_summarise_empty(self):
        s = summarise([])
        assert s["n"] == 0
        assert s["pass_rate"] == 0.0

    def test_summarise_full(self):
        runs = [
            _r("t1", "m1", 1.0),  # pass
            _r("t1", "m1", 0.5),  # partial
            _r("t1", "m1", 0.0),  # fail
            _r("t1", "m1", 1.0),  # pass
        ]
        s = summarise(runs)
        assert s["n"] == 4
        assert s["n_passed"] == 2
        assert s["mean_score"] == 0.625
        assert s["pass_rate"] == 0.5

    def test_summarise_errors_counted(self):
        runs = [
            _r("t1", "m1", 1.0),
            _r("t1", "m1", 0.0, error="API"),
        ]
        s = summarise(runs)
        assert s["n_errored"] == 1
        assert s["error_rate"] == 0.5

    def test_filter_window_drops_old(self):
        from datetime import datetime, timedelta, timezone
        old_ts = (
            datetime.now(timezone.utc) - timedelta(days=30)
        ).isoformat()
        fresh_ts = datetime.now(timezone.utc).isoformat()
        runs = [
            _r("t1", "m1", 1.0, ts=old_ts),
            _r("t1", "m1", 1.0, ts=fresh_ts),
        ]
        kept = filter_runs(runs, window_days=7)
        assert len(kept) == 1

    def test_filter_by_task_and_model(self):
        runs = [
            _r("t1", "m1", 1.0),
            _r("t1", "m2", 1.0),
            _r("t2", "m1", 1.0),
        ]
        assert len(filter_runs(runs, task_id="t1")) == 2
        assert len(filter_runs(runs, model="m1")) == 2
        assert len(filter_runs(runs, task_id="t1", model="m1")) == 1

    def test_per_model_groups(self):
        runs = [
            _r("t1", "m1", 1.0),
            _r("t1", "m2", 0.0),
            _r("t2", "m1", 0.5),
        ]
        g = per_model(runs)
        assert "m1" in g
        assert "m2" in g
        assert g["m1"]["n"] == 2
        assert g["m1"]["mean_score"] == 0.75

    def test_per_task_groups(self):
        runs = [
            _r("t1", "m1", 1.0),
            _r("t1", "m2", 0.5),
            _r("t2", "m1", 0.0),
        ]
        g = per_task(runs)
        assert g["t1"]["mean_score"] == 0.75
        assert g["t2"]["mean_score"] == 0.0

    def test_per_task_and_model_matrix(self):
        runs = [
            _r("t1", "m1", 1.0),
            _r("t1", "m1", 0.0),
            _r("t1", "m2", 1.0),
        ]
        g = per_task_and_model(runs)
        assert g[("t1", "m1")]["mean_score"] == 0.5
        assert g[("t1", "m2")]["mean_score"] == 1.0

    def test_leaderboard_shape(self):
        from datetime import datetime, timezone
        fresh = datetime.now(timezone.utc).isoformat()
        runs = [
            _r("t1", "m1", 1.0, ts=fresh),
            _r("t1", "m2", 0.5, ts=fresh),
        ]
        lb = leaderboard(runs, window_days=30)
        assert "by_model" in lb
        assert "by_task" in lb
        assert "matrix" in lb
        assert lb["n_runs"] == 2
        # Sorted descending by mean_score
        model_keys = list(lb["by_model"].keys())
        assert model_keys[0] == "m1"  # 1.0 > 0.5


# ============================================================================
# Scheduler job — host-friendly subset
# ============================================================================


class TestSchedulerJob:
    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path):
        benchmark_store.reset_for_tests(tmp_path / "bench")
        # Make sure the scheduler state lands in our isolated dir
        from app.benchmarks import scheduler_job
        scheduler_job.reset_state_for_tests()
        yield
        benchmark_store.reset_for_tests(None)
        scheduler_job.reset_state_for_tests()

    def test_master_switch_off_is_noop(self, monkeypatch):
        # Force the master switch OFF by stubbing the getter
        from app.benchmarks import scheduler_job

        def _disabled():
            return False
        monkeypatch.setattr(scheduler_job, "_is_enabled", _disabled)
        out = scheduler_job.run_refresh()
        assert out["ran"] is False
        assert out["skipped_reason"] == "master_switch_off"

    def test_force_bypasses_master_switch(self, monkeypatch):
        from app.benchmarks import scheduler_job

        def _disabled():
            return False
        monkeypatch.setattr(scheduler_job, "_is_enabled", _disabled)
        out = scheduler_job.run_refresh(
            force=True,
            llm_call=_make_stub_llm(output="<dummy>"),
        )
        # Force runs even when master switch is off — but the
        # scheduler should produce SOME work (n_runs > 0 because
        # the real catalog has tasks).
        assert out["ran"] is True
        assert out["n_runs"] > 0

    def test_force_bypasses_cadence(self, monkeypatch):
        from app.benchmarks import scheduler_job

        # First pass with a stub llm — record cadence
        monkeypatch.setattr(scheduler_job, "_is_enabled", lambda: True)
        first = scheduler_job.run_refresh(
            llm_call=_make_stub_llm(output="x"),
        )
        assert first["ran"] is True

        # Second pass without force — cadence guards
        second = scheduler_job.run_refresh(
            llm_call=_make_stub_llm(output="x"),
        )
        assert second["ran"] is False
        assert "cadence" in second["skipped_reason"]

        # Third pass with force — fires
        third = scheduler_job.run_refresh(
            force=True,
            llm_call=_make_stub_llm(output="x"),
        )
        assert third["ran"] is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
