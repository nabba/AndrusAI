"""Tests for app.upgrade_lifecycle.smokes.chromadb (Gap 2 reference runner).

The smoke runs the BUMPED chromadb in the trial venv as a subprocess
against a copy of a real production snapshot. These tests inject the
subprocess + snapshot-locator so they exercise every branch without
touching the real chromadb library or workspace.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import pytest

from app.upgrade_lifecycle.smokes import chromadb as smoke_cdb
from app.upgrade_lifecycle import smokes as smokes_mod


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    """Sandbox with a fake .trial_venv/bin/python so the smoke can find it."""
    venv_bin = tmp_path / ".trial_venv" / "bin"
    venv_bin.mkdir(parents=True)
    py = venv_bin / "python"
    py.write_text("")
    py.chmod(0o755)
    return tmp_path


@pytest.fixture
def fake_snapshot(tmp_path: Path) -> Path:
    """A throwaway file masquerading as a chroma.sqlite3 snapshot."""
    snap_dir = tmp_path / "fake_kb" / ".sqlite_snapshots"
    snap_dir.mkdir(parents=True)
    snap = snap_dir / "20260525T120000Z.db"
    snap.write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)
    return snap


def _completed(rc: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=rc, stdout=stdout, stderr=stderr,
    )


# ── 1: Auto-registration on import ──────────────────────────────────────


def test_chromadb_runner_auto_registers_on_module_import():
    """Importing the module wires the runner into the registry for
    package 'chromadb'."""
    # The fixture above + this module-level import already exercised it,
    # but we explicitly assert here.
    runners = smokes_mod.runners_for("chromadb")
    assert smoke_cdb.run in runners


# ── 2: No snapshot available → ok with skip note ────────────────────────


def test_no_snapshot_yields_ok_skip(sandbox):
    """First-run grace — no snapshot under workspace, smoke reports
    ok with a skip note (doesn't penalize the trial)."""
    result = smoke_cdb.run(
        sandbox,
        locate_snapshot=lambda: None,
    )
    assert result["status"] == "ok"
    assert "skipped" in result["details"]
    assert result["name"] == "chromadb"


# ── 3: Locator raising → error row ──────────────────────────────────────


def test_locator_exception_yields_error(sandbox):
    def _raise():
        raise RuntimeError("workspace inaccessible")
    result = smoke_cdb.run(sandbox, locate_snapshot=_raise)
    assert result["status"] == "error"
    assert "workspace inaccessible" in result["details"]


# ── 4: Subprocess success → ok with collection count ────────────────────


def test_subprocess_ok_yields_ok_with_count(sandbox, fake_snapshot):
    def _runner(argv, timeout):
        # Verify the subprocess was invoked with the venv python +
        # the smoke scratch dir under sandbox.
        assert argv[0].endswith("python")
        scratch_arg = Path(argv[-1])
        assert (scratch_arg / "chroma.sqlite3").exists()
        return _completed(0, stdout=json.dumps({"ok": True, "collections": 7}))

    result = smoke_cdb.run(
        sandbox,
        locate_snapshot=lambda: fake_snapshot,
        subprocess_runner=_runner,
    )
    assert result["status"] == "ok"
    assert result["collections"] == 7
    assert "7 collections" in result["details"]


# ── 5: Subprocess returns {ok: false} → fail with diagnostic ────────────


def test_subprocess_chromadb_refused_yields_fail(sandbox, fake_snapshot):
    def _runner(argv, timeout):
        return _completed(0, stdout=json.dumps(
            {"ok": False, "error": "schema version mismatch"}))

    result = smoke_cdb.run(
        sandbox,
        locate_snapshot=lambda: fake_snapshot,
        subprocess_runner=_runner,
    )
    assert result["status"] == "fail"
    assert "schema version mismatch" in result["details"]


# ── 6: Subprocess non-zero rc + empty stdout → fail with stderr ─────────


def test_subprocess_crashes_with_stderr(sandbox, fake_snapshot):
    def _runner(argv, timeout):
        return _completed(1, stdout="", stderr="ImportError: chromadb not found")

    result = smoke_cdb.run(
        sandbox,
        locate_snapshot=lambda: fake_snapshot,
        subprocess_runner=_runner,
    )
    assert result["status"] == "fail"
    assert "ImportError" in result["details"]


# ── 7: Subprocess timeout → error row ───────────────────────────────────


def test_subprocess_timeout_yields_error(sandbox, fake_snapshot):
    def _runner(argv, timeout):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout)

    result = smoke_cdb.run(
        sandbox,
        locate_snapshot=lambda: fake_snapshot,
        subprocess_runner=_runner,
    )
    assert result["status"] == "error"
    assert "timed out" in result["details"]


# ── 8: Subprocess launch failure → error ────────────────────────────────


def test_subprocess_launch_failure_yields_error(sandbox, fake_snapshot):
    def _runner(argv, timeout):
        raise FileNotFoundError("simulated bad path")

    result = smoke_cdb.run(
        sandbox,
        locate_snapshot=lambda: fake_snapshot,
        subprocess_runner=_runner,
    )
    assert result["status"] == "error"
    assert "simulated" in result["details"]


# ── 9: Subprocess returns garbage stdout → error ────────────────────────


def test_subprocess_garbage_output_yields_error(sandbox, fake_snapshot):
    def _runner(argv, timeout):
        return _completed(0, stdout="this is not JSON at all")

    result = smoke_cdb.run(
        sandbox,
        locate_snapshot=lambda: fake_snapshot,
        subprocess_runner=_runner,
    )
    assert result["status"] == "error"
    assert "not JSON" in result["details"]


# ── 10: Missing venv python → error before subprocess attempted ─────────


def test_missing_venv_python_yields_error(tmp_path, fake_snapshot):
    """No .trial_venv → smoke errors out cleanly before subprocess."""
    runner_called: list = []
    def _runner(argv, timeout):
        runner_called.append(argv)
        return _completed(0)

    result = smoke_cdb.run(
        tmp_path,                                    # bare sandbox, no venv
        locate_snapshot=lambda: fake_snapshot,
        subprocess_runner=_runner,
    )
    assert result["status"] == "error"
    assert "venv python missing" in result["details"]
    assert runner_called == []                       # never reached subprocess


# ── 11: Snapshot copy failure → error ───────────────────────────────────


def test_snapshot_copy_failure_yields_error(sandbox, monkeypatch):
    """If shutil.copy raises, the smoke records an error row (e.g., disk
    full, permission denied) instead of crashing the trial."""
    fake_path = Path("/nonexistent/snapshot.db")
    def _locate():
        # Bypass the .exists() check by returning a path that exists in
        # the test view via a stub.
        return SimpleNamespace(  # type: ignore[return-value]
            exists=lambda: True,
            __fspath__=lambda: str(fake_path),
        )

    # Force the existence check to pass but the copy to fail.
    def _copy_raises(src, dst):
        raise PermissionError("read-only filesystem")
    monkeypatch.setattr(smoke_cdb.shutil, "copy", _copy_raises)

    # The locate_snapshot must return a Path-like with .exists() True.
    # The simplest production-like stub is a real file:
    real_snap = sandbox / "real_snapshot.db"
    real_snap.write_bytes(b"x")

    result = smoke_cdb.run(
        sandbox,
        locate_snapshot=lambda: real_snap,
        subprocess_runner=lambda argv, timeout: _completed(0),
    )
    assert result["status"] == "error"
    assert "snapshot copy failed" in result["details"]


# ── 12: Default snapshot locator finds newest across KBs ────────────────


def test_default_locate_snapshot_picks_newest_across_kbs(tmp_path, monkeypatch):
    """Walk every ``*/.sqlite_snapshots/`` and pick the newest by mtime."""
    # Simulate two KBs with snapshots.
    kb_a = tmp_path / "kb_a" / ".sqlite_snapshots"
    kb_b = tmp_path / "kb_b" / ".sqlite_snapshots"
    kb_a.mkdir(parents=True)
    kb_b.mkdir(parents=True)
    older = kb_a / "20260101T000000Z.db"
    newer = kb_b / "20260525T000000Z.db"
    older.write_bytes(b"old")
    newer.write_bytes(b"new")

    # Stagger mtimes so 'newer' is strictly more recent.
    import os, time
    now = time.time()
    os.utime(older, (now - 86400, now - 86400))
    os.utime(newer, (now, now))

    # Make the locator's WORKSPACE_ROOT point at our tmp_path. The
    # function reads ``app.paths.WORKSPACE_ROOT`` first; fall back to
    # WORKSPACE_ROOT env var.
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    # Force the import to bypass the cached paths module (otherwise the
    # function may pick up the production WORKSPACE_ROOT).
    monkeypatch.setattr(
        "app.paths.WORKSPACE_ROOT", tmp_path, raising=False,
    )

    found = smoke_cdb._default_locate_snapshot()
    assert found is not None
    assert found.name == newer.name
