"""Tests for app.upgrade_lifecycle.trial_runner (U3).

PROGRAM §62. Covers:

  1.  Happy path — pip + pytest succeed → status ok
  2.  Test failure → status test_failure, fail_count > 0
  3.  Install failure (pip rc != 0) → status install_failure
  4.  pytest binary missing (rc 127) → status infrastructure_error
  5.  pytest timeout (rc 124) → status timeout
  6.  Pip install timeout → status timeout
  7.  _bump_requirement handles existing pin
  8.  _bump_requirement appends when absent
  9.  _bump_requirement preserves comments + blank lines
  10. _parse_pytest_output extracts pass/fail counts
  11. _parse_pytest_output collects FAILED test names
  12. Master switch OFF → status infrastructure_error
  13. Tempdir cleanup happens even on exception
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from app.upgrade_lifecycle import trial_runner as tr


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def stub_repo(tmp_path: Path) -> Path:
    """Minimal repo layout: app/, tests/, conftest.py, requirements.txt."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_smoke.py").write_text("def test_smoke():\n    assert True\n")
    (tmp_path / "conftest.py").write_text("")
    (tmp_path / "requirements.txt").write_text("starlette==0.52.1\nfastapi==0.110.0\n")
    return tmp_path


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(tr, "_enabled", lambda: True)


def _make_pip(rc: int, stdout: str = "", stderr: str = "") -> Callable:
    def _pip(_cwd, _pkg, _ver, _timeout):
        return (rc, stdout, stderr)
    return _pip


def _make_pytest(rc: int, stdout: str = "", stderr: str = "") -> Callable:
    def _pytest(_cwd, _timeout):
        return (rc, stdout, stderr)
    return _pytest


# ── 1: Happy path ───────────────────────────────────────────────────────


def test_happy_path_yields_ok(stub_repo, enabled):
    result = tr.run_trial(
        package="starlette", from_version="0.52.1", to_version="1.0.1",
        repo_root=stub_repo,
        pip_installer=_make_pip(0),
        pytest_runner=_make_pytest(0, stdout="13 passed in 0.42s\n"),
    )
    assert result.status == "ok"
    assert result.pass_count == 13
    assert result.fail_count == 0


# ── 2: Test failure ─────────────────────────────────────────────────────


def test_failed_tests_yield_test_failure_status(stub_repo, enabled):
    pytest_out = (
        "FAILED tests/test_x.py::test_a\n"
        "FAILED tests/test_x.py::test_b\n"
        "10 passed, 2 failed in 1.23s\n"
    )
    result = tr.run_trial(
        package="starlette", from_version="0.52.1", to_version="1.0.1",
        repo_root=stub_repo,
        pip_installer=_make_pip(0),
        pytest_runner=_make_pytest(1, stdout=pytest_out),
    )
    assert result.status == "test_failure"
    assert result.pass_count == 10
    assert result.fail_count == 2
    assert "tests/test_x.py::test_a" in result.failures
    assert "tests/test_x.py::test_b" in result.failures


# ── 3: Install failure ──────────────────────────────────────────────────


def test_pip_failure_yields_install_failure_status(stub_repo, enabled):
    result = tr.run_trial(
        package="starlette", from_version="0.52.1", to_version="1.0.1",
        repo_root=stub_repo,
        pip_installer=_make_pip(1, stderr="ERROR: could not find a version"),
        pytest_runner=_make_pytest(0),
    )
    assert result.status == "install_failure"
    assert any("could not find" in f for f in result.failures)


# ── 4: pytest binary missing ────────────────────────────────────────────


def test_pytest_missing_yields_infrastructure_error(stub_repo, enabled):
    result = tr.run_trial(
        package="starlette", from_version="0.52.1", to_version="1.0.1",
        repo_root=stub_repo,
        pip_installer=_make_pip(0),
        pytest_runner=_make_pytest(127, stderr="pytest binary not found"),
    )
    assert result.status == "infrastructure_error"


# ── 5: pytest timeout ───────────────────────────────────────────────────


def test_pytest_timeout_yields_timeout_status(stub_repo, enabled):
    result = tr.run_trial(
        package="starlette", from_version="0.52.1", to_version="1.0.1",
        repo_root=stub_repo,
        pip_installer=_make_pip(0),
        pytest_runner=_make_pytest(124, stderr="pytest timed out"),
    )
    assert result.status == "timeout"


# ── 6: Pip install timeout ──────────────────────────────────────────────


def test_pip_timeout_yields_timeout_status(stub_repo, enabled):
    result = tr.run_trial(
        package="starlette", from_version="0.52.1", to_version="1.0.1",
        repo_root=stub_repo,
        pip_installer=_make_pip(124, stderr="pip install timed out"),
        pytest_runner=_make_pytest(0),
    )
    assert result.status == "timeout"


# ── 7-9: _bump_requirement ──────────────────────────────────────────────


def test_bump_requirement_updates_existing_pin():
    text = "starlette==0.52.1\nfastapi==0.110.0\n"
    out = tr._bump_requirement(text, "starlette", "1.0.1")
    assert "starlette==1.0.1" in out
    assert "starlette==0.52.1" not in out
    assert "fastapi==0.110.0" in out


def test_bump_requirement_appends_when_absent():
    text = "fastapi==0.110.0\n"
    out = tr._bump_requirement(text, "starlette", "1.0.1")
    assert "starlette==1.0.1" in out
    assert "fastapi==0.110.0" in out


def test_bump_requirement_preserves_comments_and_blanks():
    text = (
        "# this is a comment\n"
        "\n"
        "starlette==0.52.1\n"
        "# another comment\n"
        "fastapi==0.110.0\n"
    )
    out = tr._bump_requirement(text, "starlette", "1.0.1")
    assert "# this is a comment" in out
    assert "# another comment" in out
    assert out.count("\n\n") >= 1   # blank line preserved


def test_bump_requirement_case_insensitive():
    text = "Starlette==0.52.1\n"
    out = tr._bump_requirement(text, "starlette", "1.0.1")
    assert "==1.0.1" in out
    assert "0.52.1" not in out


# ── 10-11: _parse_pytest_output ─────────────────────────────────────────


def test_parse_pytest_output_extracts_pass_fail_counts():
    out = "13 passed, 2 failed in 1.23s\n"
    p, f, _ = tr._parse_pytest_output(out, "")
    assert p == 13
    assert f == 2


def test_parse_pytest_output_handles_passed_only():
    out = "42 passed in 0.10s\n"
    p, f, _ = tr._parse_pytest_output(out, "")
    assert p == 42
    assert f == 0


def test_parse_pytest_output_collects_failed_names():
    out = (
        "FAILED tests/x.py::test_one\n"
        "FAILED tests/y.py::test_two[A-1]\n"
        "10 passed, 2 failed in 1.0s\n"
    )
    _, _, failures = tr._parse_pytest_output(out, "")
    assert "tests/x.py::test_one" in failures
    assert "tests/y.py::test_two[A-1]" in failures


def test_parse_pytest_output_counts_errors_into_fail():
    out = "10 passed, 1 failed, 2 errors in 1.0s\n"
    p, f, _ = tr._parse_pytest_output(out, "")
    assert p == 10
    assert f == 3   # 1 failed + 2 errors


# ── 12: Master switch OFF ───────────────────────────────────────────────


def test_master_switch_off_returns_infrastructure_error(stub_repo, monkeypatch):
    monkeypatch.setattr(tr, "_enabled", lambda: False)
    result = tr.run_trial(
        package="starlette", from_version="0.52.1", to_version="1.0.1",
        repo_root=stub_repo,
        pip_installer=_make_pip(0),
        pytest_runner=_make_pytest(0),
    )
    assert result.status == "infrastructure_error"


# ── 13: tempdir cleanup after exception ─────────────────────────────────


def test_tempdir_cleanup_after_exception(stub_repo, enabled, monkeypatch):
    """When the installer raises mid-trial, the tempdir is still cleaned up."""
    called: list[str] = []

    original_mkdtemp = tr.tempfile.mkdtemp

    def _track_mkdtemp(*args, **kwargs):
        path = original_mkdtemp(*args, **kwargs)
        called.append(path)
        return path

    monkeypatch.setattr(tr.tempfile, "mkdtemp", _track_mkdtemp)

    def _failing_pip(_cwd, _pkg, _ver, _timeout):
        raise RuntimeError("simulated pip crash")

    result = tr.run_trial(
        package="x", from_version="1", to_version="2",
        repo_root=stub_repo,
        pip_installer=_failing_pip,
        pytest_runner=_make_pytest(0),
    )
    assert result.status == "infrastructure_error"
    # All tempdirs cleaned up
    for tmpdir in called:
        assert not Path(tmpdir).exists(), f"tempdir leak: {tmpdir}"
