"""Tests for app.upgrade_lifecycle.package_manager (P2#b)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.upgrade_lifecycle import package_manager as pm


@pytest.fixture
def repo(tmp_path):
    return tmp_path


# ── Detection ───────────────────────────────────────────────────────────


def test_detect_pip_via_requirements(repo):
    (repo / "requirements.txt").write_text("starlette==0.52\n")
    result = pm.detect_manager(repo)
    assert result.manager == pm.PackageManager.PIP
    assert result.evidence_path == "requirements.txt"


def test_detect_uv_via_lockfile(repo):
    (repo / "uv.lock").write_text("# uv lock\n")
    result = pm.detect_manager(repo)
    assert result.manager == pm.PackageManager.UV
    assert result.confidence == "explicit_lock"


def test_detect_poetry_via_lockfile(repo):
    (repo / "poetry.lock").write_text("# poetry lock\n")
    assert pm.detect_manager(repo).manager == pm.PackageManager.POETRY


def test_detect_pdm_via_lockfile(repo):
    (repo / "pdm.lock").write_text("")
    assert pm.detect_manager(repo).manager == pm.PackageManager.PDM


def test_lockfile_beats_pyproject(repo):
    (repo / "pyproject.toml").write_text("[tool.poetry]\nname='x'\n")
    (repo / "uv.lock").write_text("")
    # uv.lock wins (most specific evidence)
    assert pm.detect_manager(repo).manager == pm.PackageManager.UV


def test_detect_poetry_via_pyproject(repo):
    (repo / "pyproject.toml").write_text("[tool.poetry]\nname = 'x'\n")
    result = pm.detect_manager(repo)
    assert result.manager == pm.PackageManager.POETRY
    assert result.confidence == "config_only"


def test_detect_pdm_via_pyproject(repo):
    (repo / "pyproject.toml").write_text("[tool.pdm]\npython_version = '>=3.11'\n")
    assert pm.detect_manager(repo).manager == pm.PackageManager.PDM


def test_default_is_pip_when_nothing_present(repo):
    result = pm.detect_manager(repo)
    assert result.manager == pm.PackageManager.PIP
    assert result.confidence == "default"
    assert result.evidence_path is None


# ── Install command shape ──────────────────────────────────────────────


def test_pip_install_with_requirements_file(repo):
    req = repo / "requirements.txt"
    req.write_text("starlette==0.52\n")
    venv_py = repo / ".venv" / "bin" / "python"
    cmd = pm.install_command(
        pm.PackageManager.PIP,
        venv_python=venv_py,
        package="starlette", version="1.0.1",
        requirements_file=req,
    )
    assert "-m" in cmd and "pip" in cmd
    assert "-r" in cmd
    assert str(req) in cmd


def test_pip_install_without_requirements_file(repo):
    venv_py = repo / ".venv" / "bin" / "python"
    cmd = pm.install_command(
        pm.PackageManager.PIP,
        venv_python=venv_py,
        package="starlette", version="1.0.1",
    )
    assert "starlette==1.0.1" in cmd
    assert "-r" not in cmd


def test_uv_install_uses_uv_module(repo):
    venv_py = repo / ".venv" / "bin" / "python"
    cmd = pm.install_command(
        pm.PackageManager.UV,
        venv_python=venv_py,
        package="starlette", version="1.0.1",
    )
    assert "uv" in cmd
    assert "pip" in cmd
    assert "install" in cmd
    assert "starlette==1.0.1" in cmd


def test_poetry_install_falls_back_to_pip(repo):
    venv_py = repo / ".venv" / "bin" / "python"
    cmd = pm.install_command(
        pm.PackageManager.POETRY,
        venv_python=venv_py,
        package="starlette", version="1.0.1",
    )
    # Poetry mode still drives via pip inside the venv for trial purposes
    assert "pip" in cmd
    assert "install" in cmd


# ── Writer applicability ───────────────────────────────────────────────


def test_writer_handles_pip_with_requirements():
    detection = pm.DetectionResult(
        manager=pm.PackageManager.PIP,
        evidence_path="requirements.txt",
        confidence="explicit_lock",
    )
    assert pm.writer_can_handle(detection) is True


def test_writer_handles_pip_default():
    detection = pm.DetectionResult(
        manager=pm.PackageManager.PIP,
        evidence_path=None, confidence="default",
    )
    assert pm.writer_can_handle(detection) is True


def test_writer_refuses_uv():
    detection = pm.DetectionResult(
        manager=pm.PackageManager.UV,
        evidence_path="uv.lock", confidence="explicit_lock",
    )
    assert pm.writer_can_handle(detection) is False


def test_writer_refuses_poetry():
    detection = pm.DetectionResult(
        manager=pm.PackageManager.POETRY,
        evidence_path="poetry.lock", confidence="explicit_lock",
    )
    assert pm.writer_can_handle(detection) is False
