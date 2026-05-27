"""Tests for the verified implementer (app/self_improvement/verified_implementer.py).

Uses a REAL git worktree + REAL pytest (via a tiny fake manager that just cuts a
detached worktree) so the edit→test→verify→diff flow is exercised end-to-end,
not mocked. The load-bearing test is ``test_scaffold_that_drops_api_is_caught``:
it proves the engine rejects the exact failure mode the old pipeline shipped — a
tidy file that drops the public API.

Skips cleanly when git or the app import chain is unavailable.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

if shutil.which("git") is None:  # pragma: no cover
    pytest.skip("git not available", allow_module_level=True)

try:
    from app.self_improvement.change_spec import build_change_spec
    from app.self_improvement.verified_implementer import implement_change
    from app.coding_session.iterate import IterateConfig
    from app.code_intel import store as ci_store
except Exception as exc:  # pragma: no cover - host without full env
    pytest.skip(f"app import unavailable: {exc}", allow_module_level=True)


def _no_fix(**kwargs):
    """Diagnosis stub — never proposes a fix, so the iterate loop terminates
    immediately on a red test without any LLM call."""
    return None


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True, text=True)


class _FakeManager:
    """Cuts a real detached git worktree and returns a session-shaped object —
    exercises the real runner without the quota/store machinery."""

    def __init__(self, repo: Path, work: Path):
        self.repo = repo
        self.work = work
        self._n = 0

    def start(self, *, agent_id, base, purpose, worktree_root):
        self._n += 1
        wt = self.work / f"sess{self._n}"
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(wt), base],
            cwd=str(self.repo),
            check=True,
            capture_output=True,
            text=True,
        )
        return SimpleNamespace(id=f"sess{self._n}", worktree_path=str(wt))


# Fast, hermetic loop bounds: no type-check, tiny budget.
_CFG = IterateConfig(max_iterations=3, budget_usd=0.05, run_type_check=False)


@pytest.fixture()
def repo_and_spec(tmp_path):
    repo = tmp_path / "repo"
    (repo / "app" / "crews").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "app" / "__init__.py").write_text("")
    (repo / "app" / "crews" / "__init__.py").write_text("")
    (repo / "app" / "crews" / "widget.py").write_text(
        "import json\n\n\n"
        "class Widget:\n"
        "    def run(self, x):\n"
        "        return json.dumps(x)\n\n\n"
        "def helper():\n"
        "    return Widget()\n"
    )
    (repo / "tests" / "test_widget.py").write_text(
        "from app.crews.widget import Widget\n\n\n"
        "def test_run():\n"
        "    assert Widget().run(1) == '1'\n"
    )
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")

    ci_store.reset_for_tests(tmp_path / "ci")
    work = tmp_path / "work"
    work.mkdir()
    try:
        spec = build_change_spec("app/crews/widget.py", root=repo, refresh=True)
        yield repo, spec, work
    finally:
        ci_store.reset_for_tests(None)


def test_good_edit_goes_green(repo_and_spec):
    repo, spec, work = repo_and_spec

    def editor(spec, approach):
        return spec.full_source.replace(
            "class Widget:", "class Widget:\n    VERSION = 2"
        )

    res = implement_change(
        spec,
        "add a VERSION attribute",
        editor_fn=editor,
        manager=_FakeManager(repo, work),
        config=_CFG,
        diagnosis_fn=_no_fix,
    )

    assert res.status == "green", res.notes
    assert res.api_preserved
    assert res.succeeded
    assert "app/crews/widget.py" in res.changed_files
    assert "VERSION = 2" in res.changed_file_contents["app/crews/widget.py"]
    # The synthesized smoke test is untracked → excluded from the proposed change.
    assert not any("_api_preservation" in f for f in res.changed_files)


def test_scaffold_that_drops_api_is_caught(repo_and_spec):
    """The exact old failure mode: a clean file that silently drops the public
    API. The old engine scored this +0.0133 and queued it; here it's rejected."""
    repo, spec, work = repo_and_spec

    def scaffold_editor(spec, approach):
        return "class Widget:\n    pass\n"  # drops run(), helper(), import json

    res = implement_change(
        spec,
        "refactor the crew",
        editor_fn=scaffold_editor,
        manager=_FakeManager(repo, work),
        config=_CFG,
        diagnosis_fn=_no_fix,
    )

    assert res.status == "api_broken", res.notes
    assert not res.api_preserved
    assert not res.succeeded


def test_noop_edit_is_rejected(repo_and_spec):
    repo, spec, work = repo_and_spec

    res = implement_change(
        spec,
        "do nothing",
        editor_fn=lambda s, a: s.full_source,
        manager=_FakeManager(repo, work),
        config=_CFG,
        diagnosis_fn=_no_fix,
    )

    assert res.status == "edit_failed"
    assert not res.succeeded
