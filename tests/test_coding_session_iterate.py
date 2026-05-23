"""Tests for the iterate-until-green primitive (2026-05-20).

Covers Phase 2 piece 2g — the test-driven loop:
  * First-iteration green → "passed"
  * Multi-iteration convergence
  * Max-iterations exhaustion
  * Budget exhaustion
  * Diagnosis declines (None / declined=True / is_actionable=False)
  * Test runner crash → "test_runner_error"
  * File reader / writer crash
  * Diagnosis_fn crash isolated to the iteration
  * fixes_applied log accurately tracks each iteration

Safety invariants pinned:
  * Loop always returns an IterateOutcome (never raises) for the
    exhaustion / no-fix paths.
  * Test runner exceptions surface as "test_runner_error" status, not
    silently looped past.
  * Budget check runs BEFORE the diagnosis call (so we never pay
    for a call we can't afford to apply).
"""
from __future__ import annotations

import sys
import types
import unittest
from dataclasses import dataclass
from unittest.mock import MagicMock

# Stubs (defensive — defer to real crewai when available)
_mock_psycopg2 = MagicMock()
_mock_psycopg2.InterfaceError = type("InterfaceError", (Exception,), {})
_mock_psycopg2.OperationalError = type("OperationalError", (Exception,), {})
sys.modules.setdefault("psycopg2", _mock_psycopg2)
sys.modules.setdefault("psycopg2.pool", MagicMock())

try:
    import crewai as _real_crewai  # noqa: F401
    _crewai_available = True
except Exception:
    _crewai_available = False

if not _crewai_available:
    for _mod in ("crewai", "crewai.tools"):
        if _mod not in sys.modules:
            m = types.ModuleType(_mod)
            if _mod == "crewai.tools":
                m.tool = lambda name: (lambda fn: fn)
                m.BaseTool = type("BaseTool", (), {})
            sys.modules[_mod] = m


from app.coding_session.iterate import (  # noqa: E402
    IterateConfig,
    IterateOutcome,
    iterate_until_green,
)


# ── Stubs for RunResult + StructuredFix shapes ──────────────────────


@dataclass(frozen=True)
class StubRunResult:
    """Minimal RunResult-shape for tests."""
    ok: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
        }


@dataclass(frozen=True)
class StubFix:
    """Mimic StructuredFix.is_actionable property."""
    path: str
    new_content: str
    old_content: str = ""
    confidence: float = 0.85
    reasoning: str = ""
    declined: bool = False
    decline_reason: str = ""

    @property
    def is_actionable(self) -> bool:
        return (not self.declined) and bool(self.new_content) and bool(self.path)


# ── Test scaffolding ────────────────────────────────────────────────


class _InMemoryWorktree:
    """In-memory file store + counters so tests can pin behaviour
    without touching disk."""

    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self.files: dict[str, str] = dict(initial or {})
        self.reads: list[str] = []
        self.writes: list[tuple[str, str]] = []

    def read(self, path: str) -> str:
        self.reads.append(path)
        return self.files.get(path, "")

    def write(self, path: str, content: str) -> None:
        self.writes.append((path, content))
        self.files[path] = content


def _green() -> StubRunResult:
    return StubRunResult(ok=True, exit_code=0, stdout="all tests passed")


def _red(stderr: str = "AssertionError: x != y") -> StubRunResult:
    return StubRunResult(ok=False, exit_code=1, stderr=stderr)


# ============================================================================
# Happy path
# ============================================================================


class TestIterateFirstTryGreen(unittest.TestCase):
    def test_passes_on_first_iteration(self):
        wt = _InMemoryWorktree({"x.py": "def f(): return 1"})
        outcome = iterate_until_green(
            target_file="x.py",
            test_runner=_green,
            file_reader=wt.read,
            file_writer=wt.write,
            diagnosis_fn=lambda **kw: self.fail(
                "diagnosis must not be called when test is green",
            ),
        )
        self.assertEqual(outcome.status, "passed")
        self.assertEqual(outcome.iterations, 0)
        self.assertEqual(outcome.fixes_applied, [])
        self.assertEqual(outcome.cost_usd, 0.0)

    def test_passes_on_third_iteration_after_two_fixes(self):
        wt = _InMemoryWorktree({"x.py": "def f(): return 1"})
        calls: list[int] = []

        def _runner():
            calls.append(1)
            # Red, red, then green.
            return _green() if len(calls) >= 3 else _red()

        diag_calls: list[int] = []

        def _diag(**kw):
            diag_calls.append(1)
            return StubFix(path="x.py", new_content=f"# fix {len(diag_calls)}\n")

        outcome = iterate_until_green(
            target_file="x.py",
            test_runner=_runner,
            file_reader=wt.read,
            file_writer=wt.write,
            diagnosis_fn=_diag,
        )
        self.assertEqual(outcome.status, "passed")
        self.assertEqual(outcome.iterations, 2)  # third iteration is i=2
        self.assertEqual(len(outcome.fixes_applied), 2)
        self.assertEqual(outcome.fixes_applied[0]["iteration"], 0)
        self.assertEqual(outcome.fixes_applied[1]["iteration"], 1)
        # Each fix has the path + confidence
        self.assertEqual(outcome.fixes_applied[0]["path"], "x.py")
        self.assertEqual(outcome.fixes_applied[0]["confidence"], 0.85)
        # Writes happened in the worktree
        self.assertEqual(len(wt.writes), 2)


# ============================================================================
# Exhaustion paths
# ============================================================================


class TestMaxIterationsExhaustion(unittest.TestCase):
    def test_red_never_passes_hits_max_iter(self):
        wt = _InMemoryWorktree({"x.py": "stub"})
        cfg = IterateConfig(max_iterations=5, budget_usd=100.0)
        outcome = iterate_until_green(
            target_file="x.py",
            test_runner=lambda: _red("forever red"),
            file_reader=wt.read,
            file_writer=wt.write,
            diagnosis_fn=lambda **kw: StubFix(
                path="x.py", new_content="# tweak",
            ),
            config=cfg,
        )
        self.assertEqual(outcome.status, "max_iterations")
        self.assertEqual(outcome.iterations, 5)
        self.assertEqual(len(outcome.fixes_applied), 5)
        self.assertIn("max_iterations=5", outcome.last_decline_reason)


# ============================================================================
# Budget exhaustion
# ============================================================================


class TestBudgetExhaustion(unittest.TestCase):
    def test_budget_check_stops_before_diagnosis_call(self):
        wt = _InMemoryWorktree({"x.py": "stub"})
        diag_calls = []

        def _diag(**kw):
            diag_calls.append(1)
            return StubFix(path="x.py", new_content="# tweak")

        # Budget = $0.0005, cost_per_diagnosis = $0.001. After 0
        # diagnoses cost=0, next would cost $0.001 > budget → stop
        # before calling diagnosis at all.
        cfg = IterateConfig(
            max_iterations=10,
            budget_usd=0.0005,
            cost_per_diagnosis_usd=0.001,
        )
        outcome = iterate_until_green(
            target_file="x.py",
            test_runner=lambda: _red(),
            file_reader=wt.read,
            file_writer=wt.write,
            diagnosis_fn=_diag,
            config=cfg,
        )
        self.assertEqual(outcome.status, "budget_exhausted")
        self.assertEqual(diag_calls, [])  # never called
        self.assertEqual(outcome.cost_usd, 0.0)

    def test_budget_exhaustion_after_several_iterations(self):
        wt = _InMemoryWorktree({"x.py": "stub"})
        # Budget $0.003 + cost $0.001 → 3 diagnoses possible
        # (1st @ $0.001, 2nd @ $0.002, 3rd @ $0.003, 4th would
        # exceed $0.003 budget after consume).
        cfg = IterateConfig(
            max_iterations=100,
            budget_usd=0.003,
            cost_per_diagnosis_usd=0.001,
        )
        outcome = iterate_until_green(
            target_file="x.py",
            test_runner=lambda: _red(),
            file_reader=wt.read,
            file_writer=wt.write,
            diagnosis_fn=lambda **kw: StubFix(
                path="x.py", new_content="# tweak",
            ),
            config=cfg,
        )
        self.assertEqual(outcome.status, "budget_exhausted")
        # 3 diagnoses consumed + a 4th refused by budget guard
        self.assertEqual(len(outcome.fixes_applied), 3)
        self.assertAlmostEqual(outcome.cost_usd, 0.003, places=6)


# ============================================================================
# Diagnosis declines
# ============================================================================


class TestDiagnosisDeclines(unittest.TestCase):
    def test_diagnosis_returns_none_stops_loop(self):
        wt = _InMemoryWorktree({"x.py": "stub"})
        outcome = iterate_until_green(
            target_file="x.py",
            test_runner=lambda: _red(),
            file_reader=wt.read,
            file_writer=wt.write,
            diagnosis_fn=lambda **kw: None,
        )
        self.assertEqual(outcome.status, "no_fix_available")
        self.assertIn("returned None", outcome.last_decline_reason)

    def test_diagnosis_declined_true_stops_loop(self):
        wt = _InMemoryWorktree({"x.py": "stub"})
        outcome = iterate_until_green(
            target_file="x.py",
            test_runner=lambda: _red(),
            file_reader=wt.read,
            file_writer=wt.write,
            diagnosis_fn=lambda **kw: StubFix(
                path="", new_content="", declined=True,
                decline_reason="ambiguous multi-site bug",
            ),
        )
        self.assertEqual(outcome.status, "no_fix_available")
        self.assertIn("multi-site", outcome.last_decline_reason)

    def test_diagnosis_not_actionable_stops_loop(self):
        # Fix with empty new_content → is_actionable=False
        wt = _InMemoryWorktree({"x.py": "stub"})
        outcome = iterate_until_green(
            target_file="x.py",
            test_runner=lambda: _red(),
            file_reader=wt.read,
            file_writer=wt.write,
            diagnosis_fn=lambda **kw: StubFix(
                path="x.py", new_content="",
            ),
        )
        self.assertEqual(outcome.status, "no_fix_available")

    def test_diagnosis_fn_raising_stops_loop(self):
        wt = _InMemoryWorktree({"x.py": "stub"})

        def _boom(**kw):
            raise RuntimeError("LLM unreachable")

        outcome = iterate_until_green(
            target_file="x.py",
            test_runner=lambda: _red(),
            file_reader=wt.read,
            file_writer=wt.write,
            diagnosis_fn=_boom,
        )
        self.assertEqual(outcome.status, "no_fix_available")
        self.assertIn("LLM unreachable", outcome.last_decline_reason)


# ============================================================================
# Runner / reader / writer crashes
# ============================================================================


class TestRunnerCrash(unittest.TestCase):
    def test_runner_exception_returns_test_runner_error(self):
        wt = _InMemoryWorktree({"x.py": "stub"})

        def _boom():
            raise RuntimeError("subprocess died")

        outcome = iterate_until_green(
            target_file="x.py",
            test_runner=_boom,
            file_reader=wt.read,
            file_writer=wt.write,
            diagnosis_fn=lambda **kw: StubFix(
                path="x.py", new_content="# fix",
            ),
        )
        self.assertEqual(outcome.status, "test_runner_error")
        self.assertIn("subprocess died", outcome.error_text)

    def test_file_reader_exception_stops_loop(self):
        def _bad_reader(p):
            raise OSError("permission denied")

        outcome = iterate_until_green(
            target_file="x.py",
            test_runner=lambda: _red(),
            file_reader=_bad_reader,
            file_writer=lambda p, c: None,
            diagnosis_fn=lambda **kw: StubFix(
                path="x.py", new_content="# fix",
            ),
        )
        self.assertEqual(outcome.status, "no_fix_available")
        self.assertIn("permission denied", outcome.error_text)

    def test_file_writer_exception_stops_loop(self):
        wt = _InMemoryWorktree({"x.py": "stub"})

        def _bad_writer(p, c):
            raise OSError("disk full")

        outcome = iterate_until_green(
            target_file="x.py",
            test_runner=lambda: _red(),
            file_reader=wt.read,
            file_writer=_bad_writer,
            diagnosis_fn=lambda **kw: StubFix(
                path="x.py", new_content="# fix",
            ),
        )
        self.assertEqual(outcome.status, "no_fix_available")
        self.assertIn("disk full", outcome.error_text)


# ============================================================================
# Outcome serialisation
# ============================================================================


class TestOutcomeSerialisation(unittest.TestCase):
    def test_as_jsonable_contains_all_fields(self):
        outcome = IterateOutcome(
            status="passed",
            iterations=2,
            cost_usd=0.0024,
            fixes_applied=[
                {"iteration": 0, "path": "x.py", "confidence": 0.85,
                 "reasoning": "off-by-one"},
            ],
            last_test_result={"ok": True},
        )
        d = outcome.as_jsonable()
        self.assertEqual(d["status"], "passed")
        self.assertEqual(d["iterations"], 2)
        self.assertEqual(d["cost_usd"], 0.0024)
        self.assertEqual(len(d["fixes_applied"]), 1)
        self.assertEqual(d["last_test_result"]["ok"], True)


# ============================================================================
# Defensive shape handling
# ============================================================================


class TestDefensiveShapeHandling(unittest.TestCase):
    def test_runner_without_to_dict_still_serialises(self):
        # A test runner stub that doesn't implement to_dict — the
        # loop's defensive _result_to_dict falls back to a minimal
        # projection.
        class _Bare:
            ok = False
            exit_code = 1
            stderr = "barebones stub"
            stdout = ""

        outcome = iterate_until_green(
            target_file="x.py",
            test_runner=lambda: _Bare(),
            file_reader=lambda p: "stub",
            file_writer=lambda p, c: None,
            diagnosis_fn=lambda **kw: None,
        )
        self.assertEqual(outcome.status, "no_fix_available")
        self.assertEqual(outcome.last_test_result["exit_code"], 1)
        self.assertEqual(outcome.last_test_result["ok"], False)

    def test_runner_with_only_exit_code_is_green(self):
        # No ``ok`` attribute — falls back to ``exit_code == 0`` check.
        class _OnlyExit:
            exit_code = 0
            stderr = ""

        outcome = iterate_until_green(
            target_file="x.py",
            test_runner=lambda: _OnlyExit(),
            file_reader=lambda p: "stub",
            file_writer=lambda p, c: None,
            diagnosis_fn=lambda **kw: self.fail(
                "diagnosis must not be called when test is green",
            ),
        )
        self.assertEqual(outcome.status, "passed")


if __name__ == "__main__":
    unittest.main()
