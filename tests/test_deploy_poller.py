"""Deploy poller (2026-06-15) — fast-forward decision + end-to-end dispatch.

The poller (`scripts/deploy_poller.py`) runs `deploy_gateway.sh` on the host
when `origin/main` is a clean fast-forward ahead of the local checkout. It is
the pull-based alternative to the #133 inbound webhook. The security-relevant
surface is the *decision*: it must deploy ONLY on a fast-forward of the tracked
branch and never on a diverged / locally-ahead / wrong-branch tree.

`decide()` is pinned as a pure function; `check_once()` is exercised end-to-end
against a real throwaway git repo + a stub deploy script (so no Docker, no
network, no real deploy). Loaded via importlib from path with the same pattern
as `test_deploy_webhook.py`.
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="git binary not available")


def _load():
    path = Path(__file__).parent.parent / "scripts" / "deploy_poller.py"
    spec = importlib.util.spec_from_file_location("_test_deploy_poller", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_test_deploy_poller"] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Pure decision ───────────────────────────────────────────────────────────
def test_decide_remote_ahead_deploys():
    mod = _load()
    code, _ = mod.decide("main", "main", "a" * 40, "b" * 40, local_is_ancestor_of_remote=True)
    assert code == mod.DEPLOYED


def test_decide_up_to_date_skips():
    mod = _load()
    code, _ = mod.decide("main", "main", "a" * 40, "a" * 40, local_is_ancestor_of_remote=False)
    assert code == mod.UP_TO_DATE


def test_decide_wrong_branch_skips():
    mod = _load()
    code, _ = mod.decide("feature", "main", "a" * 40, "b" * 40, local_is_ancestor_of_remote=True)
    assert code == mod.WRONG_BRANCH


def test_decide_diverged_skips():
    # Different SHAs but local is NOT an ancestor of remote (diverged / local ahead).
    mod = _load()
    code, _ = mod.decide("main", "main", "a" * 40, "b" * 40, local_is_ancestor_of_remote=False)
    assert code == mod.DIVERGED


# ── Git fixture helpers ───────────────────────────────────────────────────--
def _run(*args, cwd):
    subprocess.run([GIT, *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _setup_repos(tmp_path):
    """Bare remote + a working clone with one commit on `main`."""
    remote = tmp_path / "remote.git"
    _run("init", "--bare", "-q", str(remote), cwd=tmp_path)
    work = tmp_path / "work"
    _run("clone", "-q", str(remote), str(work), cwd=tmp_path)
    _run("config", "user.email", "t@t", cwd=work)
    _run("config", "user.name", "t", cwd=work)
    (work / "f.txt").write_text("v1")
    _run("add", "-A", cwd=work)
    _run("commit", "-q", "-m", "init", cwd=work)
    _run("branch", "-M", "main", cwd=work)
    _run("push", "-q", "-u", "origin", "main", cwd=work)
    # Pin the bare repo's HEAD to main so later clones check out main cleanly.
    _run("-C", str(remote), "symbolic-ref", "HEAD", "refs/heads/main", cwd=tmp_path)
    return remote, work


def _advance_remote(tmp_path, remote):
    """Push a new commit to origin/main from a second clone."""
    other = tmp_path / "other"
    _run("clone", "-q", str(remote), str(other), cwd=tmp_path)
    _run("config", "user.email", "t@t", cwd=other)
    _run("config", "user.name", "t", cwd=other)
    (other / "f.txt").write_text("v2")
    _run("add", "-A", cwd=other)
    _run("commit", "-q", "-m", "v2", cwd=other)
    _run("push", "-q", "origin", "main", cwd=other)


def _stub_deploy(tmp_path, sentinel, exit_code=0):
    p = tmp_path / "stub_deploy.sh"
    p.write_text(f"#!/usr/bin/env bash\ntouch '{sentinel}'\nexit {exit_code}\n")
    p.chmod(0o755)
    return p


def _check(mod, work, deploy_script):
    return mod.check_once(
        repo_root=str(work), target_branch="main", remote="origin",
        git_bin=GIT, deploy_script=str(deploy_script), deploy_timeout=60, alert=None,
    )


# ── End-to-end check_once() against a real repo ──────────────────────────────
def test_check_once_remote_ahead_runs_deploy(tmp_path):
    mod = _load()
    remote, work = _setup_repos(tmp_path)
    _advance_remote(tmp_path, remote)
    sentinel = tmp_path / "deployed.marker"
    stub = _stub_deploy(tmp_path, sentinel)

    code, _ = _check(mod, work, stub)

    assert code == mod.DEPLOYED
    assert sentinel.exists(), "deploy script should have run when origin/main was ahead"


def test_check_once_up_to_date_does_not_deploy(tmp_path):
    mod = _load()
    _, work = _setup_repos(tmp_path)
    sentinel = tmp_path / "deployed.marker"
    stub = _stub_deploy(tmp_path, sentinel)

    code, _ = _check(mod, work, stub)

    assert code == mod.UP_TO_DATE
    assert not sentinel.exists()


def test_check_once_wrong_branch_does_not_deploy(tmp_path):
    mod = _load()
    _, work = _setup_repos(tmp_path)
    _run("checkout", "-q", "-b", "feature", cwd=work)
    sentinel = tmp_path / "deployed.marker"
    stub = _stub_deploy(tmp_path, sentinel)

    code, _ = _check(mod, work, stub)

    assert code == mod.WRONG_BRANCH
    assert not sentinel.exists()


def test_check_once_local_ahead_is_diverged_not_deployed(tmp_path):
    # Local main has a commit origin/main doesn't → not fast-forwardable.
    mod = _load()
    _, work = _setup_repos(tmp_path)
    (work / "local_only.txt").write_text("x")
    _run("add", "-A", cwd=work)
    _run("commit", "-q", "-m", "local only", cwd=work)
    sentinel = tmp_path / "deployed.marker"
    stub = _stub_deploy(tmp_path, sentinel)

    code, _ = _check(mod, work, stub)

    assert code == mod.DIVERGED
    assert not sentinel.exists()


def test_check_once_deploy_failure_reported(tmp_path):
    mod = _load()
    remote, work = _setup_repos(tmp_path)
    _advance_remote(tmp_path, remote)
    sentinel = tmp_path / "deployed.marker"
    stub = _stub_deploy(tmp_path, sentinel, exit_code=1)

    code, _ = _check(mod, work, stub)

    assert code == mod.DEPLOY_FAILED
    assert sentinel.exists(), "stub ran (and failed); poller should report DEPLOY_FAILED"


def test_check_once_fires_alerts_on_deploy(tmp_path):
    mod = _load()
    remote, work = _setup_repos(tmp_path)
    _advance_remote(tmp_path, remote)
    sentinel = tmp_path / "deployed.marker"
    stub = _stub_deploy(tmp_path, sentinel)
    seen = []

    code, _ = mod.check_once(
        repo_root=str(work), target_branch="main", remote="origin",
        git_bin=GIT, deploy_script=str(stub), deploy_timeout=60, alert=seen.append,
    )

    assert code == mod.DEPLOYED
    assert len(seen) == 2  # start + result
    assert "Auto-deploy" in seen[0] and "✅" in seen[1]


# ── Signal alert is a safe no-op without a configured recipient ──────────────
def test_signal_alert_noop_without_owner(monkeypatch):
    mod = _load()
    monkeypatch.delenv("SIGNAL_OWNER_NUMBER", raising=False)
    # Must not raise and must not attempt any network call.
    assert mod.signal_alert("hello") is None


# ── Collection gate (CI substitute while GitHub Actions is billing-locked) ───--
def _check_gated(mod, work, deploy_script, *, gate_fn, gate_state, alert=None):
    return mod.check_once(
        repo_root=str(work), target_branch="main", remote="origin",
        git_bin=GIT, deploy_script=str(deploy_script), deploy_timeout=60,
        alert=alert, gate_cmd="pytest --collect-only", gate_timeout=60,
        gate_state_path=str(gate_state), gate_fn=gate_fn,
    )


def test_gate_pass_allows_deploy(tmp_path):
    mod = _load()
    remote, work = _setup_repos(tmp_path)
    _advance_remote(tmp_path, remote)
    sentinel = tmp_path / "deployed.marker"
    stub = _stub_deploy(tmp_path, sentinel)

    code, _ = _check_gated(
        mod, work, stub,
        gate_fn=lambda **kw: (True, "collection clean"),
        gate_state=tmp_path / "gate.json",
    )

    assert code == mod.DEPLOYED
    assert sentinel.exists()


def test_gate_block_withholds_deploy_and_alerts_once(tmp_path):
    mod = _load()
    remote, work = _setup_repos(tmp_path)
    _advance_remote(tmp_path, remote)
    sentinel = tmp_path / "deployed.marker"
    stub = _stub_deploy(tmp_path, sentinel)
    gate_state = tmp_path / "gate.json"
    seen = []

    code, _ = _check_gated(
        mod, work, stub,
        gate_fn=lambda **kw: (False, "collection errors — ERROR tests/x.py"),
        gate_state=gate_state, alert=seen.append,
    )

    assert code == mod.GATE_BLOCKED
    assert not sentinel.exists(), "deploy must be withheld on a collection error"
    assert len(seen) == 1 and "withheld" in seen[0].lower()

    # Second tick, SAME bad SHA: silent, still no deploy, no new alert, gate not re-run.
    seen.clear()

    def _explode(**kw):
        raise AssertionError("gate must not re-run on an already-blocked SHA")

    code2, _ = _check_gated(
        mod, work, stub, gate_fn=_explode, gate_state=gate_state, alert=seen.append,
    )

    assert code2 == mod.GATE_ALREADY_BLOCKED
    assert not sentinel.exists()
    assert seen == []


def test_gate_unblocks_after_fix_commit(tmp_path):
    mod = _load()
    remote, work = _setup_repos(tmp_path)
    _advance_remote(tmp_path, remote)
    sentinel = tmp_path / "deployed.marker"
    stub = _stub_deploy(tmp_path, sentinel)
    gate_state = tmp_path / "gate.json"

    # First (bad) SHA is blocked.
    _check_gated(mod, work, stub, gate_fn=lambda **kw: (False, "errs"), gate_state=gate_state)
    assert not sentinel.exists()

    # A fix commit advances origin/main → new SHA → gate consulted again → passes → deploy.
    # (Distinct clone dir; _advance_remote's fixed "other" dir is already taken above.)
    fix = tmp_path / "fixclone"
    _run("clone", "-q", str(remote), str(fix), cwd=tmp_path)
    _run("config", "user.email", "t@t", cwd=fix)
    _run("config", "user.name", "t", cwd=fix)
    (fix / "f.txt").write_text("v3")
    _run("add", "-A", cwd=fix)
    _run("commit", "-q", "-m", "v3", cwd=fix)
    _run("push", "-q", "origin", "main", cwd=fix)
    code, _ = _check_gated(mod, work, stub, gate_fn=lambda **kw: (True, "clean"), gate_state=gate_state)

    assert code == mod.DEPLOYED
    assert sentinel.exists()


# ── collection_gate() itself, against a real throwaway worktree ──────────────
def _head_sha(work):
    return subprocess.run(
        [GIT, "rev-parse", "HEAD"], cwd=str(work), capture_output=True, text=True,
    ).stdout.strip()


def test_collection_gate_clean(tmp_path):
    mod = _load()
    _, work = _setup_repos(tmp_path)
    ok, detail = mod.collection_gate(
        repo_root=str(work), remote_sha=_head_sha(work), git_bin=GIT,
        gate_cmd="exit 0", gate_timeout=60,
    )
    assert ok and "clean" in detail


def test_collection_gate_blocks_on_collection_error(tmp_path):
    mod = _load()
    _, work = _setup_repos(tmp_path)
    ok, detail = mod.collection_gate(
        repo_root=str(work), remote_sha=_head_sha(work), git_bin=GIT,
        gate_cmd="echo '5 tests collected, 1 error'; exit 2", gate_timeout=60,
    )
    assert ok is False and "collection error" in detail.lower()


def test_collection_gate_fails_open_when_pytest_cannot_run(tmp_path):
    # rc 2 but no "collected" in output → can't confirm a real collection error →
    # fail OPEN rather than wedge every deploy on a broken gate environment.
    mod = _load()
    _, work = _setup_repos(tmp_path)
    ok, _ = mod.collection_gate(
        repo_root=str(work), remote_sha=_head_sha(work), git_bin=GIT,
        gate_cmd="exit 2", gate_timeout=60,
    )
    assert ok is True


def test_collection_gate_disabled_is_noop(tmp_path):
    mod = _load()
    _, work = _setup_repos(tmp_path)
    ok, detail = mod.collection_gate(
        repo_root=str(work), remote_sha="HEAD", git_bin=GIT, gate_cmd="", gate_timeout=60,
    )
    assert ok and "disabled" in detail
