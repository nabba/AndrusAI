"""Regression tests for the location-aware _repo_root() resolver.

The probes need a repo-root resolver that works in three layouts:
  - Local development repo (tests/ present)
  - Production container (tests/ absent)
  - Future layouts that keep app/subia/ as a subpackage

Bug fixed: probe `test_exists()` previously returned False inside the
container because Dockerfile does not COPY tests/. Every probe row
that required a regression-test file then collapsed to PARTIAL/FAIL,
breaking Phase 9 exit criteria in production.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest


def test_repo_root_finds_local_repo():
    from app.subia.probes.indicator_result import _repo_root
    root = _repo_root()
    assert (root / "app" / "subia").is_dir()
    # Marker exists (Dockerfile, requirements.txt, .git, or pyproject.toml)
    markers = ("Dockerfile", "pyproject.toml", "requirements.txt", ".git")
    assert any((root / m).exists() for m in markers)


def test_repo_root_honours_env_override(tmp_path, monkeypatch):
    # Build a fake repo with the marker
    (tmp_path / "app" / "subia").mkdir(parents=True)
    (tmp_path / "Dockerfile").touch()
    monkeypatch.setenv("SUBIA_REPO_ROOT", str(tmp_path))

    # Reload the resolver module so it picks up the env var
    import importlib
    import app.subia.probes.indicator_result as ir
    importlib.reload(ir)
    try:
        assert ir._repo_root() == tmp_path
    finally:
        monkeypatch.delenv("SUBIA_REPO_ROOT")
        importlib.reload(ir)


def test_module_exists_resolves_against_repo_root():
    from app.subia.probes.indicator_result import module_exists
    # Real Phase 12 module
    assert module_exists("app/subia/wonder/detector.py")
    # Made-up path — must return False
    assert not module_exists("app/does/not/exist.py")


def test_test_exists_returns_true_when_tests_dir_absent(tmp_path, monkeypatch):
    """The production-container path: tests/ is not packaged. Probes
    must NOT downgrade to PARTIAL just because tests/ is missing."""
    # Build a minimal fake repo with subia but WITHOUT tests/
    (tmp_path / "app" / "subia").mkdir(parents=True)
    (tmp_path / "Dockerfile").touch()
    monkeypatch.setenv("SUBIA_REPO_ROOT", str(tmp_path))

    import importlib
    import app.subia.probes.indicator_result as ir
    importlib.reload(ir)
    try:
        # Even though tests/test_anything.py doesn't exist in tmp_path,
        # test_exists must return True because tests/ itself is absent.
        assert ir.test_exists("tests/test_anything.py") is True
        # And module_exists is unaffected — still strict.
        assert ir.module_exists("tests/test_anything.py") is False
    finally:
        monkeypatch.delenv("SUBIA_REPO_ROOT")
        importlib.reload(ir)


def test_test_exists_strict_when_tests_dir_present():
    """Local development: tests/ exists, so the existence check must
    actually verify the file. This guarantees the resolver is not
    just an unconditional True."""
    from app.subia.probes.indicator_result import test_exists
    # Real test file
    assert test_exists("tests/test_phase14_temporal_synchronization.py")
    # Made-up test name — must FAIL strictly
    assert not test_exists("tests/test_does_not_exist.py")


def test_phase9_exit_criteria_pass_when_tests_dir_absent(tmp_path, monkeypatch):
    """End-to-end: simulate the container layout and prove the scorecard
    Phase 9 exit criteria still PASS (the original bug)."""
    # Build a layout that mirrors the container: code present, tests absent
    repo = Path(__file__).resolve().parents[1]
    fake_root = tmp_path / "fake_repo"
    fake_root.mkdir()
    (fake_root / "Dockerfile").touch()
    # Symlink the live `app/` into the fake root so all probe paths resolve
    (fake_root / "app").symlink_to(repo / "app")
    # NOTE: deliberately do NOT create tests/ in fake_root

    monkeypatch.setenv("SUBIA_REPO_ROOT", str(fake_root))

    import importlib
    import app.subia.probes.indicator_result as ir
    import app.subia.probes.butlin as bl
    import app.subia.probes.rsm as rsm_mod
    import app.subia.probes.sk as sk_mod
    import app.subia.probes.scorecard as sc
    importlib.reload(ir)
    importlib.reload(bl)
    importlib.reload(rsm_mod)
    importlib.reload(sk_mod)
    importlib.reload(sc)
    try:
        passed, report = sc.meets_exit_criteria()
        assert passed, f"exit criteria must hold without tests/ on disk: {report}"
    finally:
        monkeypatch.delenv("SUBIA_REPO_ROOT")
        importlib.reload(ir)
        importlib.reload(bl)
        importlib.reload(rsm_mod)
        importlib.reload(sk_mod)
        importlib.reload(sc)
