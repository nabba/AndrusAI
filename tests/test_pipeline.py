"""End-to-end test of the verified mutation engine (app/self_improvement/pipeline.py).

Real git + real pytest on both baseline and candidate worktrees; only the LLM
editor/judge are stubbed. Proves the composed verdict is right for the cases
that matter:
  * a real bug-fix (failing test → passing)          → IMPROVED (correctness)
  * a scaffold that drops the API                     → REJECT
  * a clean cosmetic change with no benchmark         → INVARIANTS_ONLY
  * a quality win measured through the entry point     → IMPROVED (quality)
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
    from app.self_improvement.pipeline import run_pipeline
    from app.coding_session.iterate import IterateConfig
    from app.code_intel import store as ci_store
except Exception as exc:  # pragma: no cover
    pytest.skip(f"app import unavailable: {exc}", allow_module_level=True)


_CFG = IterateConfig(max_iterations=3, budget_usd=0.05, run_type_check=False)

_TEST = (
    "from app.crews.widget import Widget\n\n\n"
    "def test_run():\n    assert Widget().run(1) == '1'\n"
)
_CLEAN = (
    "import json\n\n\n"
    "class Widget:\n    def run(self, x):\n        return json.dumps(x)\n\n\n"
    "def helper():\n    return Widget()\n"
)
_BUGGY = _CLEAN.replace("return json.dumps(x)", 'return json.dumps(x) + "_BUG"')


def _no_fix(**kwargs):
    return None


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True, text=True)


class _FakeManager:
    def __init__(self, repo: Path, work: Path):
        self.repo, self.work, self._n = repo, work, 0

    def start(self, *, agent_id, base, purpose, worktree_root):
        self._n += 1
        wt = self.work / f"sess{self._n}"
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(wt), base],
            cwd=str(self.repo), check=True, capture_output=True, text=True,
        )
        return SimpleNamespace(id=f"sess{self._n}", worktree_path=str(wt))


@pytest.fixture()
def make_repo(tmp_path):
    n = {"i": 0}
    ci_store.reset_for_tests(tmp_path / "ci")

    def _make(widget_src: str):
        i = n["i"]; n["i"] += 1
        repo = tmp_path / f"repo{i}"
        (repo / "app" / "crews").mkdir(parents=True)
        (repo / "tests").mkdir()
        (repo / "app" / "__init__.py").write_text("")
        (repo / "app" / "crews" / "__init__.py").write_text("")
        (repo / "app" / "crews" / "widget.py").write_text(widget_src)
        (repo / "tests" / "test_widget.py").write_text(_TEST)
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "t@t.t")
        _git(repo, "config", "user.name", "t")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "init")
        work = tmp_path / f"work{i}"; work.mkdir()
        return repo, work, _FakeManager(repo, work)

    try:
        yield _make
    finally:
        ci_store.reset_for_tests(None)


def test_real_bugfix_is_improved(make_repo):
    repo, work, mgr = make_repo(_BUGGY)  # covering test FAILS at HEAD

    def fix_editor(spec, approach):
        return spec.full_source.replace(' + "_BUG"', "")

    res = run_pipeline(
        "app/crews/widget.py", "remove the stray _BUG suffix",
        editor_fn=fix_editor, manager=mgr, root=repo, config=_CFG, diagnosis_fn=_no_fix,
    )

    assert res.status == "green", res.notes
    assert res.verdict["verdict"] == "IMPROVED", res.verdict
    assert res.verdict["correctness_delta"] == 1
    assert res.proposable
    assert "app/crews/widget.py" in res.changed_file_contents


def test_scaffold_is_rejected(make_repo):
    repo, work, mgr = make_repo(_CLEAN)

    res = run_pipeline(
        "app/crews/widget.py", "refactor",
        editor_fn=lambda s, a: "class Widget:\n    pass\n",
        manager=mgr, root=repo, config=_CFG, diagnosis_fn=_no_fix,
    )

    assert res.status == "api_broken", res.notes
    assert res.verdict["verdict"] == "REJECT"
    assert not res.proposable


def test_clean_change_no_benchmark_is_invariants_only(make_repo):
    repo, work, mgr = make_repo(_CLEAN)  # covering test PASSES at HEAD

    res = run_pipeline(
        "app/crews/widget.py", "add a clarifying comment",
        editor_fn=lambda s, a: "# tweak\n" + s.full_source,
        manager=mgr, root=repo, config=_CFG, diagnosis_fn=_no_fix,
    )

    assert res.status == "green", res.notes
    assert res.verdict["verdict"] == "INVARIANTS_ONLY", res.verdict
    assert res.proposable  # correctness proven; operator decides on value


def test_quality_win_via_entry_point_is_improved(make_repo):
    repo, work, mgr = make_repo(_CLEAN)

    def boost_editor(spec, approach):
        return "# QUALITY_BOOST\n" + spec.full_source

    def entry_point_runner(code_root, task):
        # Simulates the real entry point: its output reflects the code in this
        # worktree. Candidate carries the marker; baseline doesn't.
        return (Path(code_root) / "app" / "crews" / "widget.py").read_text()

    def judge_call(prompt):
        return "0.9" if "QUALITY_BOOST" in prompt else "0.4"

    tasks = [{"id": f"q{i}", "input": "do the thing", "rubric": "good"} for i in range(4)]

    res = run_pipeline(
        "app/crews/widget.py", "improve quality",
        editor_fn=boost_editor, manager=mgr, root=repo, config=_CFG, diagnosis_fn=_no_fix,
        entry_point_runner=entry_point_runner, judge_call=judge_call, quality_tasks=tasks,
    )

    assert res.status == "green", res.notes
    assert res.verdict["verdict"] == "IMPROVED", res.verdict
    assert res.verdict["quality_delta"] is not None and res.verdict["quality_delta"] > 0.05
    assert res.proposable
