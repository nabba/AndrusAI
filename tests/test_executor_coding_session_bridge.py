"""Tests for the executor ↔ coding-session bridge (2026-05-20).

Covers Phase 2 piece 2h:
  * Context primitives: ContextVar + set_executor_context + getter
  * executor_agent_id formatting
  * cleanup_sessions_for_run: discards matching sessions, skips
    non-matching, handles empty store gracefully
  * Manager.start accepts durable parameter
  * coding_session_tools._resolve_agent_id branches on executor_run
  * commander_adapter sets the context around the dispatch
  * driver._finalise calls cleanup on terminal transitions

Safety invariants pinned:
  * Default behaviour unchanged when no executor context is bound
  * ContextVar is properly reset after `with` exits
  * cleanup only discards sessions whose agent_id starts with
    `executor:<run_id>:` — never touches arbitrary sessions
  * Driver cleanup never raises; failure isolated
"""
from __future__ import annotations

import sys
import time
import types
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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


from app.autonomous_executor.coding_session_bridge import (  # noqa: E402
    cleanup_sessions_for_run,
    current_executor_run_id,
    executor_agent_id,
    is_executor_active,
    set_executor_context,
)


# ── Context primitives ──────────────────────────────────────────────


class TestContextPrimitives(unittest.TestCase):
    def test_default_is_none(self):
        # Outside any context, no run_id is bound.
        self.assertIsNone(current_executor_run_id())
        self.assertFalse(is_executor_active())

    def test_set_executor_context_binds_run_id(self):
        with set_executor_context("run-abc"):
            self.assertEqual(current_executor_run_id(), "run-abc")
            self.assertTrue(is_executor_active())
        # Reset on exit
        self.assertIsNone(current_executor_run_id())
        self.assertFalse(is_executor_active())

    def test_empty_run_id_is_noop(self):
        with set_executor_context(""):
            self.assertIsNone(current_executor_run_id())

    def test_nested_contexts_stack(self):
        with set_executor_context("outer"):
            self.assertEqual(current_executor_run_id(), "outer")
            with set_executor_context("inner"):
                self.assertEqual(current_executor_run_id(), "inner")
            # Outer restored after inner exits
            self.assertEqual(current_executor_run_id(), "outer")
        self.assertIsNone(current_executor_run_id())

    def test_exception_in_block_still_resets(self):
        try:
            with set_executor_context("xyz"):
                raise RuntimeError("kaboom")
        except RuntimeError:
            pass
        self.assertIsNone(current_executor_run_id())


# ── executor_agent_id format ────────────────────────────────────────


class TestExecutorAgentId(unittest.TestCase):
    def test_default_role(self):
        result = executor_agent_id("run-abc")
        self.assertEqual(result, "executor:run-abc:coder")

    def test_custom_role(self):
        result = executor_agent_id("run-abc", role="researcher")
        self.assertEqual(result, "executor:run-abc:researcher")

    def test_empty_run_id_rejected(self):
        with self.assertRaises(ValueError):
            executor_agent_id("")


# ── cleanup_sessions_for_run ────────────────────────────────────────


@dataclass
class _FakeSession:
    id: str
    agent_id: str
    is_active: bool = True


class _FakeManager:
    def __init__(self) -> None:
        self.discarded: list[tuple[str, str]] = []
        self.removed_worktrees: list[str] = []

    def discard(self, session_id: str, *, reason: str) -> object:
        self.discarded.append((session_id, reason))
        return object()

    def remove_worktree(self, session) -> tuple[bool, str | None]:
        self.removed_worktrees.append(getattr(session, "id", ""))
        return True, None


class TestCleanupSessionsForRun(unittest.TestCase):
    def _patch_store_active(self, sessions):
        from app.coding_session import store
        return patch.object(
            store, "list_all",
            return_value=list(sessions),
        )

    def test_empty_run_id_is_noop(self):
        summary = cleanup_sessions_for_run("")
        self.assertEqual(summary["scanned"], 0)
        self.assertEqual(summary["discarded"], 0)

    def test_discards_matching_sessions(self):
        sessions = [
            _FakeSession(id="s1", agent_id="executor:run-A:coder"),
            _FakeSession(id="s2", agent_id="executor:run-A:coder"),
            _FakeSession(id="s3", agent_id="coder"),  # not executor
            _FakeSession(id="s4", agent_id="executor:run-B:coder"),  # different run
        ]
        mgr = _FakeManager()
        with self._patch_store_active(sessions):
            summary = cleanup_sessions_for_run("run-A", manager=mgr)
        self.assertEqual(summary["scanned"], 2)
        self.assertEqual(summary["discarded"], 2)
        self.assertEqual({d[0] for d in mgr.discarded}, {"s1", "s2"})
        # Non-matching sessions left alone
        self.assertNotIn("s3", {d[0] for d in mgr.discarded})
        self.assertNotIn("s4", {d[0] for d in mgr.discarded})

    def test_skips_already_terminal(self):
        sessions = [
            _FakeSession(
                id="s1", agent_id="executor:run-A:coder", is_active=False,
            ),
        ]
        mgr = _FakeManager()
        with self._patch_store_active(sessions):
            summary = cleanup_sessions_for_run("run-A", manager=mgr)
        self.assertEqual(summary["scanned"], 1)
        self.assertEqual(summary["skipped_terminal"], 1)
        self.assertEqual(summary["discarded"], 0)
        self.assertEqual(mgr.discarded, [])

    def test_discard_failure_counted_as_error(self):
        class _BoomManager:
            def discard(self, session_id, *, reason):
                raise RuntimeError("boom")
            def remove_worktree(self, session):
                return True, None

        sessions = [
            _FakeSession(id="s1", agent_id="executor:run-A:coder"),
        ]
        mgr = _BoomManager()
        with self._patch_store_active(sessions):
            summary = cleanup_sessions_for_run("run-A", manager=mgr)
        self.assertEqual(summary["scanned"], 1)
        self.assertEqual(summary["discarded"], 0)
        self.assertEqual(summary["errors"], 1)

    def test_store_unavailable_returns_empty_summary(self):
        # Simulate list_all raising — function returns empty summary
        # without crashing.
        from app.coding_session import store
        with patch.object(
            store, "list_all",
            side_effect=RuntimeError("store broken"),
        ):
            summary = cleanup_sessions_for_run(
                "run-A", manager=_FakeManager(),
            )
        self.assertEqual(summary["scanned"], 0)

    def test_remove_worktree_failure_still_counts_discard(self):
        # remove_worktree failure shouldn't make the cleanup fail.
        class _PartialManager:
            def __init__(self):
                self.discarded = []
            def discard(self, session_id, *, reason):
                self.discarded.append(session_id)
            def remove_worktree(self, session):
                raise RuntimeError("worktree gone")

        sessions = [
            _FakeSession(id="s1", agent_id="executor:run-A:coder"),
        ]
        mgr = _PartialManager()
        with self._patch_store_active(sessions):
            summary = cleanup_sessions_for_run("run-A", manager=mgr)
        self.assertEqual(summary["discarded"], 1)
        self.assertEqual(summary["errors"], 0)


# ── Manager.start durable parameter ────────────────────────────────


class FakeBackend:
    def __init__(self) -> None:
        self.refs = {"main": "a" * 40}

    def resolve_ref(self, ref):
        if ref not in self.refs:
            raise ValueError(f"unknown ref {ref!r}")
        return self.refs[ref]

    def create_worktree(self, *, worktree_path, base_sha):
        pass

    def remove_worktree(self, *, worktree_path, force=True):
        pass


@pytest.fixture
def store_dir(tmp_path, monkeypatch):
    from app.coding_session import store as _store
    monkeypatch.setattr(_store, "_STORE_DIR", tmp_path)
    monkeypatch.setattr(_store, "_AUDIT_LOG", tmp_path / "audit.jsonl")
    _store.reset_for_tests()
    return tmp_path


def test_manager_start_default_not_durable(store_dir, tmp_path):
    from app.coding_session import Manager, QuotaConfig
    mgr = Manager(
        backend=FakeBackend(),
        config=QuotaConfig(
            per_agent_active=5, system_active=5,
            per_session_disk_bytes=1024, system_disk_bytes=4096,
        ),
    )
    cs = mgr.start(
        agent_id="coder", base="main", purpose="legacy test",
        worktree_root=tmp_path / "wt",
    )
    assert cs.durable is False


def test_manager_start_with_durable_true(store_dir, tmp_path):
    from app.coding_session import Manager, QuotaConfig
    mgr = Manager(
        backend=FakeBackend(),
        config=QuotaConfig(
            per_agent_active=5, system_active=5,
            per_session_disk_bytes=1024, system_disk_bytes=4096,
        ),
    )
    cs = mgr.start(
        agent_id="executor:run-A:coder", base="main",
        purpose="executor-spawned",
        worktree_root=tmp_path / "wt",
        durable=True,
    )
    assert cs.durable is True


# ── coding_session_tools._resolve_agent_id ─────────────────────────


class TestResolveAgentId(unittest.TestCase):
    def test_returns_coder_by_default(self):
        from app.tools.coding_session_tools import _resolve_agent_id
        self.assertEqual(_resolve_agent_id(), "coder")

    def test_returns_executor_prefixed_when_run_supplied(self):
        from app.tools.coding_session_tools import _resolve_agent_id
        self.assertEqual(
            _resolve_agent_id(executor_run="run-abc"),
            "executor:run-abc:coder",
        )

    def test_none_executor_run_yields_default(self):
        from app.tools.coding_session_tools import _resolve_agent_id
        self.assertEqual(_resolve_agent_id(executor_run=None), "coder")


# ── Commander adapter wiring ───────────────────────────────────────


class TestCommanderAdapterSetsContext(unittest.TestCase):
    def test_adapter_binds_context_for_duration_of_call(self):
        from app.autonomous_executor import (
            ExecutorRun, ExecutorStep,
            make_commander_adapter,
        )

        observed_run: list[str | None] = []

        class _Commander:
            def handle(self, **kwargs):
                observed_run.append(current_executor_run_id())
                return "ok"

        adapter = make_commander_adapter(
            commander_provider=lambda: _Commander(),
        )
        run = ExecutorRun(
            run_id="run-bridge-test",
            goal="test",
            requestor="t",
        )
        adapter(
            ExecutorStep(step_id="s1", description="x"),
            run,
        )
        self.assertEqual(observed_run, ["run-bridge-test"])
        # Context cleared after adapter returns
        self.assertIsNone(current_executor_run_id())


# ── Driver finalise → cleanup integration ──────────────────────────


class TestDriverFinaliseCleanup(unittest.TestCase):
    def _make_run(self):
        from app.autonomous_executor import ExecutorRun
        return ExecutorRun(
            run_id=f"run-{time.time_ns()}",
            goal="x",
            requestor="t",
        )

    def test_completed_run_triggers_cleanup(self):
        from app.autonomous_executor import (
            CommanderResult, ExecutorStatus, advance_one_step,
        )

        run = self._make_run()
        commander = lambda step, r: CommanderResult(text="done")

        with patch(
            "app.autonomous_executor.coding_session_bridge.cleanup_sessions_for_run",
        ) as mock_cleanup:
            mock_cleanup.return_value = {
                "scanned": 0, "discarded": 0,
                "skipped_terminal": 0, "errors": 0,
            }
            # 1st advance: CREATED → RUNNING with one step
            advance_one_step(run)
            # 2nd advance: execute the step → finalise → COMPLETED
            advance_one_step(run, commander_fn=commander)
            self.assertEqual(run.status, ExecutorStatus.COMPLETED)
            # Cleanup invoked exactly once with the run_id
            mock_cleanup.assert_called_once_with(run.run_id)

    def test_failed_run_triggers_cleanup(self):
        from app.autonomous_executor import (
            ExecutorStatus, advance_one_step,
        )

        run = self._make_run()

        def _boom(step, r):
            raise RuntimeError("step exploded")

        with patch(
            "app.autonomous_executor.coding_session_bridge.cleanup_sessions_for_run",
        ) as mock_cleanup:
            mock_cleanup.return_value = {
                "scanned": 0, "discarded": 0,
                "skipped_terminal": 0, "errors": 0,
            }
            advance_one_step(run)
            advance_one_step(run, commander_fn=_boom)
            self.assertEqual(run.status, ExecutorStatus.FAILED)
            mock_cleanup.assert_called_once_with(run.run_id)

    def test_cleanup_exception_doesnt_crash_driver(self):
        from app.autonomous_executor import (
            CommanderResult, ExecutorStatus, advance_one_step,
        )

        run = self._make_run()
        commander = lambda step, r: CommanderResult(text="done")

        with patch(
            "app.autonomous_executor.coding_session_bridge.cleanup_sessions_for_run",
            side_effect=RuntimeError("cleanup exploded"),
        ):
            advance_one_step(run)
            # Should not raise — driver isolates the failure.
            advance_one_step(run, commander_fn=commander)
            self.assertEqual(run.status, ExecutorStatus.COMPLETED)


if __name__ == "__main__":
    unittest.main()
