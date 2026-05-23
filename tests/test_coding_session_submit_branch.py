"""Tests for the branch-mode submit path (Phase 2 piece 2i, 2026-05-20).

Covers:
  * Default submit_mode="per-file" is bit-identical to today
  * submit_mode="branch" routes to backend.submit_as_branch
  * Backend without submit_as_branch raises IllegalTransition
  * Branch name / PR title / PR body defaults derived from session
  * Backend failure marks session SUBMITTED with status="error"
  * Backend success marks session SUBMITTED with status="branch_submitted"
  * PR URL surfaces in the result (when gh succeeds)
  * Worktree cleanup runs on both success + failure paths
  * Invalid submit_mode rejected upfront
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

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


class FakeBackendBranch:
    """Backend with submit_as_branch support — for branch-mode tests."""

    def __init__(self, *, branch_result: dict | None = None) -> None:
        self.refs: dict[str, str] = {"main": "a" * 40}
        self.created: list[dict] = []
        self.removed: list[dict] = []
        self.branch_calls: list[dict] = []
        self.changes: list[tuple[str, str]] = []
        # Default: branch submit succeeds with a fake PR URL.
        self._branch_result = branch_result or {
            "ok": True,
            "commit_sha": "abcdef1234567890",
            "pr_url": "https://github.com/owner/repo/pull/42",
            "error": "",
        }

    def resolve_ref(self, ref: str) -> str:
        if ref not in self.refs:
            raise ValueError(f"unknown ref {ref!r}")
        return self.refs[ref]

    def create_worktree(self, *, worktree_path: str, base_sha: str) -> None:
        self.created.append({"path": worktree_path, "sha": base_sha})

    def remove_worktree(self, *, worktree_path: str, force: bool = True) -> None:
        self.removed.append({"path": worktree_path, "force": force})

    def list_changed_paths(
        self, *, worktree_path: str,
    ) -> list[tuple[str, str]]:
        return list(self.changes)

    def submit_as_branch(
        self,
        *,
        worktree_path: str,
        branch: str,
        commit_message: str,
        pr_title: str,
        pr_body: str,
    ) -> dict[str, Any]:
        self.branch_calls.append({
            "worktree_path": worktree_path,
            "branch": branch,
            "commit_message": commit_message,
            "pr_title": pr_title,
            "pr_body": pr_body,
        })
        return dict(self._branch_result)


class FakeBackendNoBranch:
    """Backend without submit_as_branch — simulates LocalWorktreeBackend."""

    def __init__(self) -> None:
        self.refs: dict[str, str] = {"main": "a" * 40}

    def resolve_ref(self, ref: str) -> str:
        if ref not in self.refs:
            raise ValueError(f"unknown ref {ref!r}")
        return self.refs[ref]

    def create_worktree(self, *, worktree_path: str, base_sha: str) -> None:
        pass

    def remove_worktree(self, *, worktree_path: str, force: bool = True) -> None:
        pass

    def list_changed_paths(
        self, *, worktree_path: str,
    ) -> list[tuple[str, str]]:
        return []


@pytest.fixture
def store_dir(tmp_path, monkeypatch):
    from app.coding_session import store as _store
    monkeypatch.setattr(_store, "_STORE_DIR", tmp_path)
    monkeypatch.setattr(_store, "_AUDIT_LOG", tmp_path / "audit.jsonl")
    _store.reset_for_tests()
    return tmp_path


@pytest.fixture
def manager_branch(store_dir, tmp_path):
    from app.coding_session import Manager, QuotaConfig
    cfg = QuotaConfig(
        per_agent_active=4, system_active=8,
        per_session_disk_bytes=10_240, system_disk_bytes=40_960,
        ttl_seconds=60, idle_seconds=30,
    )
    return Manager(backend=FakeBackendBranch(), config=cfg)


@pytest.fixture
def manager_nobranch(store_dir, tmp_path):
    from app.coding_session import Manager, QuotaConfig
    cfg = QuotaConfig(
        per_agent_active=4, system_active=8,
        per_session_disk_bytes=10_240, system_disk_bytes=40_960,
        ttl_seconds=60, idle_seconds=30,
    )
    return Manager(backend=FakeBackendNoBranch(), config=cfg)


def _start_session(manager, tmp_path, purpose: str = "Fix the import bug"):
    return manager.start(
        agent_id="coder",
        base="main",
        purpose=purpose,
        worktree_root=tmp_path / "wt",
    )


# ── Invalid mode + missing backend support ─────────────────────────


def test_invalid_submit_mode_rejected(manager_branch, tmp_path):
    from app.coding_session import IllegalTransition
    from app.coding_session.submit import submit_session

    cs = _start_session(manager_branch, tmp_path)
    with pytest.raises(IllegalTransition, match="invalid submit_mode"):
        submit_session(
            cs.id, submit_reason="test",
            manager=manager_branch,
            submit_mode="bogus",
        )


def test_branch_mode_refuses_without_backend_support(
    manager_nobranch, tmp_path,
):
    from app.coding_session import IllegalTransition
    from app.coding_session.submit import submit_session

    cs = _start_session(manager_nobranch, tmp_path)
    with pytest.raises(IllegalTransition, match="submit_as_branch"):
        submit_session(
            cs.id, submit_reason="test",
            manager=manager_nobranch,
            submit_mode="branch",
        )


# ── Branch mode happy path ─────────────────────────────────────────


def test_branch_mode_invokes_backend_with_defaults(
    manager_branch, tmp_path,
):
    from app.coding_session.submit import submit_session

    cs = _start_session(
        manager_branch, tmp_path,
        purpose="Fix the import bug — handle missing optional_tool_group",
    )
    updated, results = submit_session(
        cs.id, submit_reason="all tests pass",
        manager=manager_branch,
        submit_mode="branch",
    )
    # One SubmitResult, status=branch_submitted, PR URL surfaced.
    assert len(results) == 1
    assert results[0].status == "branch_submitted"
    assert results[0].path.startswith("branch:coding-session-")
    assert "github.com/owner/repo/pull/42" in results[0].refusal_reason
    assert "abcdef123456" in results[0].refusal_reason

    # Backend called with the auto-generated defaults.
    backend = manager_branch.backend
    assert len(backend.branch_calls) == 1
    call = backend.branch_calls[0]
    assert call["branch"].startswith("coding-session-")
    # PR title defaults to the first line of the purpose (truncated
    # at 72 chars per git convention).
    assert call["pr_title"].startswith("Fix the import bug")
    # PR body contains purpose + submit reason + session attribution.
    assert "all tests pass" in call["pr_body"]
    assert cs.id in call["pr_body"]


def test_branch_mode_explicit_overrides(manager_branch, tmp_path):
    from app.coding_session.submit import submit_session

    cs = _start_session(manager_branch, tmp_path)
    submit_session(
        cs.id, submit_reason="x",
        manager=manager_branch,
        submit_mode="branch",
        branch_name="my-custom-branch",
        pr_title="Custom PR title",
        pr_body="Custom PR body",
    )
    call = manager_branch.backend.branch_calls[0]
    assert call["branch"] == "my-custom-branch"
    assert call["pr_title"] == "Custom PR title"
    assert call["pr_body"] == "Custom PR body"


def test_branch_mode_cleans_up_worktree_by_default(
    manager_branch, tmp_path,
):
    from app.coding_session.submit import submit_session

    cs = _start_session(manager_branch, tmp_path)
    submit_session(
        cs.id, submit_reason="x",
        manager=manager_branch,
        submit_mode="branch",
    )
    # Backend remove_worktree called once via manager.remove_worktree.
    assert len(manager_branch.backend.removed) == 1


def test_branch_mode_skips_cleanup_when_disabled(
    manager_branch, tmp_path,
):
    from app.coding_session.submit import submit_session

    cs = _start_session(manager_branch, tmp_path)
    submit_session(
        cs.id, submit_reason="x",
        manager=manager_branch,
        submit_mode="branch",
        cleanup_worktree=False,
    )
    assert len(manager_branch.backend.removed) == 0


# ── Branch mode failure paths ──────────────────────────────────────


def test_branch_mode_backend_returns_failure(store_dir, tmp_path):
    from app.coding_session import Manager, QuotaConfig
    from app.coding_session.submit import submit_session

    backend = FakeBackendBranch(branch_result={
        "ok": False, "commit_sha": "", "pr_url": "",
        "error": "git push failed: rejected by remote",
    })
    mgr = Manager(
        backend=backend,
        config=QuotaConfig(
            per_agent_active=4, system_active=8,
            per_session_disk_bytes=10_240, system_disk_bytes=40_960,
        ),
    )
    cs = _start_session(mgr, tmp_path)
    updated, results = submit_session(
        cs.id, submit_reason="x",
        manager=mgr,
        submit_mode="branch",
    )
    # Session still transitions to SUBMITTED; result captures the error.
    assert updated.status.value == "submitted"
    assert len(results) == 1
    assert results[0].status == "error"
    assert "git push failed" in results[0].refusal_reason


def test_branch_mode_backend_raising_marks_session_failed(
    store_dir, tmp_path,
):
    from app.coding_session import (
        IllegalTransition, Manager, QuotaConfig, Status,
    )
    from app.coding_session.submit import submit_session

    class _BoomBackend(FakeBackendBranch):
        def submit_as_branch(self, **kw):
            raise RuntimeError("bridge unreachable")

    mgr = Manager(
        backend=_BoomBackend(),
        config=QuotaConfig(
            per_agent_active=4, system_active=8,
            per_session_disk_bytes=10_240, system_disk_bytes=40_960,
        ),
    )
    cs = _start_session(mgr, tmp_path)
    with pytest.raises(RuntimeError, match="bridge unreachable"):
        submit_session(
            cs.id, submit_reason="x",
            manager=mgr,
            submit_mode="branch",
        )
    # Session transitioned to FAILED (postmortem-preserving).
    from app.coding_session import store as _store
    reloaded = _store.get(cs.id)
    assert reloaded.status is Status.FAILED


def test_branch_mode_no_pr_url_still_succeeds(store_dir, tmp_path):
    from app.coding_session import Manager, QuotaConfig
    from app.coding_session.submit import submit_session

    # gh failed → no PR URL but the branch is pushed
    backend = FakeBackendBranch(branch_result={
        "ok": True, "commit_sha": "abc12345",
        "pr_url": "",
        "error": "",
    })
    mgr = Manager(
        backend=backend,
        config=QuotaConfig(
            per_agent_active=4, system_active=8,
            per_session_disk_bytes=10_240, system_disk_bytes=40_960,
        ),
    )
    cs = _start_session(mgr, tmp_path)
    updated, results = submit_session(
        cs.id, submit_reason="x",
        manager=mgr,
        submit_mode="branch",
    )
    assert results[0].status == "branch_submitted"
    assert "operator can open manually" in results[0].refusal_reason


# ── Default mode still works (regression) ──────────────────────────


def test_default_per_file_mode_unchanged(manager_branch, tmp_path):
    """The default submit_mode="per-file" still does the legacy
    per-file fanout — branch backend is not invoked."""
    from app.coding_session.submit import submit_session

    # Stage one fake change via the backend's list_changed_paths.
    manager_branch.backend.changes = [
        ("test/file.py", "modified"),
    ]
    cs = _start_session(manager_branch, tmp_path)

    # Stub the change-request port — we don't want to depend on the
    # full CR module being importable for this regression check.
    class _StubPort:
        def __init__(self):
            self.calls = []
        def create_request(self, **kw):
            self.calls.append(kw)
            class _CR:
                id = "cr-stub"
                status = "PENDING"
                def to_dict(self):
                    return {"id": self.id, "status": self.status}
            return _CR()
        def send_ask(self, request_id):
            return None

    port = _StubPort()
    updated, results = submit_session(
        cs.id, submit_reason="legacy path",
        manager=manager_branch, port=port,
    )
    # Branch backend NOT invoked
    assert len(manager_branch.backend.branch_calls) == 0
    # Per-file fanout produced ≥1 CR for the changed file
    assert any("file.py" in (sr.path or "") for sr in results)


def test_default_submit_mode_when_omitted_is_per_file(
    manager_branch, tmp_path,
):
    """Calling submit_session without submit_mode argument keeps
    legacy semantics. Pinned so future signature changes are
    conscious."""
    import inspect
    from app.coding_session.submit import submit_session

    sig = inspect.signature(submit_session)
    assert sig.parameters["submit_mode"].default == "per-file"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
